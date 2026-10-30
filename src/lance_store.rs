use std::cmp::Ordering;
use std::collections::HashSet;
use std::sync::OnceLock;
use std::time::Duration;

use arrow::array::{
    Array, ArrayRef, FixedSizeListArray, Float32Array, Int64Array, RecordBatch,
    RecordBatchIterator, StringArray,
};
use arrow::datatypes::{DataType, Field, FieldRef, Schema};
use futures::StreamExt;
use lance::dataset::builder::DatasetBuilder;
use lance::dataset::{
    Dataset, MergeInsertBuilder, NewColumnTransform, WhenMatched, WhenNotMatched, WriteMode,
    WriteParams,
};
use pgrx::check_for_interrupts;
use std::sync::Arc;
use tokio::runtime::Runtime;
use tokio::task::JoinHandle;

/// How often a blocking wait returns control to PostgreSQL so that the backend
/// can observe a cancel request or `statement_timeout`.
const INTERRUPT_POLL: Duration = Duration::from_millis(50);

pub struct SearchResult {
    pub id: i64,
    pub label: Option<String>,
    pub revision: Option<String>,
    pub distance: f32,
}

/// A Lance search started while the PostgreSQL backend runs its BM25 work.
/// Dropping the handle aborts the worker task, including on PostgreSQL errors.
pub struct PendingVectorSearch {
    task: JoinHandle<Result<Vec<SearchResult>, String>>,
}

impl PendingVectorSearch {
    pub fn wait(&mut self) -> Result<Vec<SearchResult>, Box<dyn std::error::Error>> {
        let joined = block_on(&mut self.task).map_err(|error| {
            Box::new(std::io::Error::other(error.to_string())) as Box<dyn std::error::Error>
        })?;
        joined.map_err(|error| Box::new(std::io::Error::other(error)) as Box<dyn std::error::Error>)
    }
}

impl Drop for PendingVectorSearch {
    fn drop(&mut self) {
        self.task.abort();
    }
}

pub struct ScanRow {
    pub id: i64,
    pub vector: Vec<f32>,
    pub label: Option<String>,
}

pub struct DatasetVersion {
    pub version: i64,
    pub created_ms: i64,
}

pub struct DocumentState {
    pub id: i64,
    pub label: Option<String>,
    pub revision: Option<String>,
}

/// Runtime used to drive Lance futures.
///
/// Runs inside a PostgreSQL backend process, so it is kept as small as possible:
/// a single worker thread instead of one per core. A multi-thread runtime is
/// still required because Lance's IO stack relies on `block_in_place` and the
/// tokio reactor, neither of which work from a current-thread runtime.
fn runtime() -> &'static Runtime {
    static RT: OnceLock<Runtime> = OnceLock::new();
    RT.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .worker_threads(1)
            .enable_all()
            .build()
            .expect("failed to create tokio runtime")
    })
}

/// Runs `fut` to completion while polling for PostgreSQL interrupts.
///
/// Without this every Lance operation would be uninterruptible: neither
/// `pg_cancel_backend()` nor `statement_timeout` could stop a long scan.
fn block_on<F: std::future::Future>(fut: F) -> F::Output {
    let mut fut = Box::pin(fut);
    loop {
        // The timeout must be created inside the runtime context.
        let outcome =
            runtime().block_on(async { tokio::time::timeout(INTERRUPT_POLL, &mut fut).await });
        match outcome {
            Ok(output) => return output,
            Err(_) => {
                check_for_interrupts!();
            }
        }
    }
}

fn item_field() -> FieldRef {
    Arc::new(Field::new("item", DataType::Float32, true)) as FieldRef
}

fn schema(vector_dim: i32) -> Arc<Schema> {
    Arc::new(Schema::new(vec![
        Field::new("id", DataType::Int64, false),
        Field::new(
            "vector",
            DataType::FixedSizeList(item_field(), vector_dim),
            false,
        ),
        Field::new("label", DataType::Utf8, true),
        Field::new("revision", DataType::Utf8, true),
    ]))
}

fn open_dataset(uri: &str) -> Result<Dataset, Box<dyn std::error::Error>> {
    check_for_interrupts!();
    let dataset = block_on(async { DatasetBuilder::from_uri(uri).load().await })?;
    check_for_interrupts!();
    Ok(dataset)
}

fn to_fixed_size_list(
    flat: Vec<f32>,
    vector_dim: i32,
) -> Result<FixedSizeListArray, Box<dyn std::error::Error>> {
    let values: ArrayRef = Arc::new(Float32Array::from(flat));
    Ok(FixedSizeListArray::try_new(
        item_field(),
        vector_dim,
        values,
        None,
    )?)
}

fn make_batch(
    ids: Vec<i64>,
    flat_vectors: Vec<f32>,
    labels: Vec<Option<String>>,
    revisions: Vec<Option<String>>,
    vector_dim: i32,
) -> Result<RecordBatch, Box<dyn std::error::Error>> {
    if vector_dim <= 0 {
        return Err(format!("vector_dim must be positive: {vector_dim}").into());
    }
    if ids.len() != labels.len() || ids.len() != revisions.len() {
        return Err(format!(
            "row count mismatch: {} ids, {} labels, and {} revisions",
            ids.len(),
            labels.len(),
            revisions.len()
        )
        .into());
    }
    if flat_vectors.iter().any(|value| !value.is_finite()) {
        return Err("vector values must be finite".into());
    }
    let expected_values = ids
        .len()
        .checked_mul(vector_dim as usize)
        .ok_or("vector value count overflow")?;
    if flat_vectors.len() != expected_values {
        return Err(format!(
            "vector value count mismatch: expected {}, got {}",
            expected_values,
            flat_vectors.len()
        )
        .into());
    }

    let id_array = Int64Array::from(ids);
    let vector_array = to_fixed_size_list(flat_vectors, vector_dim)?;
    let label_array = StringArray::from(labels);
    let revision_array = StringArray::from(revisions);

    let batch = RecordBatch::try_new(
        schema(vector_dim),
        vec![
            Arc::new(id_array),
            Arc::new(vector_array),
            Arc::new(label_array),
            Arc::new(revision_array),
        ],
    )?;
    Ok(batch)
}

fn ensure_revision_column(dataset: &mut Dataset) -> Result<(), Box<dyn std::error::Error>> {
    if let Some(field) = dataset.schema().field("revision") {
        if field.data_type() != DataType::Utf8 {
            return Err("Lance revision column must be UTF-8".into());
        }
        return Ok(());
    }
    block_on(async {
        dataset
            .add_columns(
                NewColumnTransform::SqlExpressions(vec![(
                    "revision".to_string(),
                    "CAST(NULL AS STRING)".to_string(),
                )]),
                None,
                None,
            )
            .await
    })?;
    Ok(())
}

fn dataset_vector_dim(dataset: &Dataset) -> Result<i32, Box<dyn std::error::Error>> {
    let field = dataset
        .schema()
        .field("vector")
        .ok_or("vector field not found")?;
    match field.data_type() {
        DataType::FixedSizeList(_, dim) => Ok(dim),
        other => Err(format!("vector field is not FixedSizeList: {other:?}").into()),
    }
}

pub fn create_dataset(
    uri: &str,
    vector_dim: i32,
    overwrite: bool,
) -> Result<(), Box<dyn std::error::Error>> {
    let batch = make_batch(vec![], vec![], vec![], vec![], vector_dim)?;
    let params = WriteParams {
        mode: if overwrite {
            WriteMode::Overwrite
        } else {
            WriteMode::Create
        },
        ..Default::default()
    };
    let reader = RecordBatchIterator::new(vec![Ok(batch)].into_iter(), schema(vector_dim));
    check_for_interrupts!();
    block_on(async { Dataset::write(reader, uri, Some(params)).await })?;
    check_for_interrupts!();
    Ok(())
}

pub fn insert_rows(
    uri: &str,
    ids: Vec<i64>,
    vectors: Vec<Vec<f32>>,
    labels: Vec<Option<String>>,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut dataset = open_dataset(uri)?;
    ensure_revision_column(&mut dataset)?;
    let vector_dim = dataset_vector_dim(&dataset)?;

    let mut flat = Vec::with_capacity(vectors.len() * vector_dim as usize);
    for v in &vectors {
        if v.len() != vector_dim as usize {
            return Err(format!(
                "vector dimension mismatch: expected {}, got {}",
                vector_dim,
                v.len()
            )
            .into());
        }
        flat.extend_from_slice(v);
    }

    let row_count = ids.len();
    let batch = make_batch(ids, flat, labels, vec![None; row_count], vector_dim)?;
    let reader = RecordBatchIterator::new(vec![Ok(batch)].into_iter(), schema(vector_dim));

    block_on(async { dataset.append(reader, None).await })?;
    check_for_interrupts!();
    Ok(())
}

pub fn insert_flat_rows(
    uri: &str,
    ids: Vec<i64>,
    flat_vectors: Vec<f32>,
    vector_dim: i32,
    labels: Vec<Option<String>>,
) -> Result<usize, Box<dyn std::error::Error>> {
    let row_count = ids.len();
    let mut dataset = open_dataset(uri)?;
    ensure_revision_column(&mut dataset)?;
    let dataset_dim = dataset_vector_dim(&dataset)?;
    if vector_dim != dataset_dim {
        return Err(format!(
            "vector dimension mismatch: dataset expects {}, got {}",
            dataset_dim, vector_dim
        )
        .into());
    }

    let batch = make_batch(ids, flat_vectors, labels, vec![None; row_count], vector_dim)?;
    let reader = RecordBatchIterator::new(vec![Ok(batch)].into_iter(), schema(vector_dim));

    block_on(async { dataset.append(reader, None).await })?;
    check_for_interrupts!();
    Ok(row_count)
}

/// Idempotent writes keyed by document ID. The SQL caller holds the same writer
/// lock used by append/restore for the entire validation and merge operation.
pub fn upsert_flat_rows(
    uri: &str,
    ids: Vec<i64>,
    flat_vectors: Vec<f32>,
    vector_dim: i32,
    labels: Vec<Option<String>>,
    revisions: Vec<Option<String>>,
) -> Result<usize, Box<dyn std::error::Error>> {
    let row_count = ids.len();
    if row_count == 0 {
        return Ok(0);
    }
    if ids.len() != labels.len() || ids.len() != revisions.len() {
        return Err("upsert requires matching ID, label, and revision counts".into());
    }
    let dataset = open_dataset(uri)?;
    let mut dataset = dataset;
    ensure_revision_column(&mut dataset)?;
    if dataset_vector_dim(&dataset)? != vector_dim {
        return Err("vector dimension mismatch in upsert".into());
    }
    let filter = format!(
        "id IN ({})",
        ids.iter().map(i64::to_string).collect::<Vec<_>>().join(",")
    );
    let mut scanner = dataset.scan();
    scanner
        .filter(&filter)?
        .project(&["id"])?
        .limit(Some((row_count + 1) as i64), None)?;
    let stream = block_on(async { scanner.try_into_stream().await })?;
    let batches = collect_batches(&mut Box::pin(stream))?;
    let mut seen = HashSet::new();
    for batch in batches {
        check_for_interrupts!();
        let values = batch
            .column(0)
            .as_any()
            .downcast_ref::<Int64Array>()
            .ok_or("id column not Int64")?;
        for index in 0..values.len() {
            if values.is_null(index) || !seen.insert(values.value(index)) {
                return Err("upsert target contains duplicate/null document IDs; reconcile legacy append data first".into());
            }
        }
    }
    let batch = make_batch(ids, flat_vectors, labels, revisions, vector_dim)?;
    let reader = RecordBatchIterator::new(vec![Ok(batch)].into_iter(), schema(vector_dim));
    let mut builder = MergeInsertBuilder::try_new(Arc::new(dataset), vec!["id".to_string()])?;
    builder
        .when_matched(WhenMatched::UpdateAll)
        .when_not_matched(WhenNotMatched::InsertAll);
    let job = builder.try_build()?;
    block_on(async { job.execute_reader(reader).await })?;
    check_for_interrupts!();
    Ok(row_count)
}

/// Physically remove every stored row with one of the supplied document IDs.
/// Lance commits a new dataset version; older versions remain available until
/// an operator explicitly runs version cleanup outside this extension.
pub fn delete_rows(uri: &str, ids: &[i64]) -> Result<usize, Box<dyn std::error::Error>> {
    if ids.is_empty() {
        return Ok(0);
    }
    let mut dataset = open_dataset(uri)?;
    let predicate = format!(
        "id IN ({})",
        ids.iter().map(i64::to_string).collect::<Vec<_>>().join(",")
    );
    let result = block_on(async { dataset.delete(&predicate).await })?;
    check_for_interrupts!();
    Ok(result.num_deleted_rows as usize)
}

pub fn scan_document_states(
    uri: &str,
    limit: i64,
) -> Result<Vec<DocumentState>, Box<dyn std::error::Error>> {
    if limit == 0 {
        return Ok(Vec::new());
    }
    let dataset = open_dataset(uri)?;
    let has_revision = dataset.schema().field("revision").is_some();
    let columns: Vec<&str> = if has_revision {
        vec!["id", "label", "revision"]
    } else {
        vec!["id", "label"]
    };
    let mut scanner = dataset.scan();
    scanner.project(&columns)?;
    if limit > 0 {
        scanner.limit(Some(limit), None)?;
    }
    let stream = block_on(async { scanner.try_into_stream().await })?;
    let batches = collect_batches(&mut Box::pin(stream))?;
    let mut rows = Vec::new();
    for batch in batches {
        check_for_interrupts!();
        let ids = batch
            .column(0)
            .as_any()
            .downcast_ref::<Int64Array>()
            .ok_or("id column not Int64")?;
        let labels = batch
            .column(1)
            .as_any()
            .downcast_ref::<StringArray>()
            .ok_or("label column not Utf8")?;
        let revisions = if has_revision {
            Some(
                batch
                    .column(2)
                    .as_any()
                    .downcast_ref::<StringArray>()
                    .ok_or("revision column not Utf8")?,
            )
        } else {
            None
        };
        for index in 0..batch.num_rows() {
            rows.push(DocumentState {
                id: ids.value(index),
                label: (!labels.is_null(index)).then(|| labels.value(index).to_string()),
                revision: revisions.and_then(|values| {
                    (!values.is_null(index)).then(|| values.value(index).to_string())
                }),
            });
        }
        if limit > 0 && rows.len() as i64 >= limit {
            rows.truncate(limit as usize);
            break;
        }
    }
    Ok(rows)
}

pub fn count_rows(uri: &str) -> Result<usize, Box<dyn std::error::Error>> {
    let dataset = open_dataset(uri)?;
    let count = block_on(async { dataset.count_rows(None).await })?;
    check_for_interrupts!();
    Ok(count)
}

pub fn vector_dim(uri: &str) -> Result<i32, Box<dyn std::error::Error>> {
    let dataset = open_dataset(uri)?;
    dataset_vector_dim(&dataset)
}

pub fn versions(uri: &str) -> Result<Vec<DatasetVersion>, Box<dyn std::error::Error>> {
    let dataset = open_dataset(uri)?;
    let versions = block_on(async { dataset.versions().await })?;
    check_for_interrupts!();
    Ok(versions
        .into_iter()
        .map(|v| DatasetVersion {
            version: v.version as i64,
            created_ms: v.timestamp.timestamp_millis(),
        })
        .collect())
}

/// Restores `version` as the dataset tip by making it the newest version again.
///
/// This is the compensation path for a write that PostgreSQL rolled back after
/// Lance already committed it.
pub fn restore_version(uri: &str, version: i64) -> Result<i64, Box<dyn std::error::Error>> {
    if version < 0 {
        return Err(format!("version must be non-negative: {version}").into());
    }
    let mut dataset = block_on(async {
        DatasetBuilder::from_uri(uri)
            .with_version(version as u64)
            .load()
            .await
    })?;
    check_for_interrupts!();
    block_on(async { dataset.restore().await })?;
    check_for_interrupts!();
    Ok(version)
}

pub fn vector_search(
    uri: &str,
    query: Vec<f32>,
    k: usize,
) -> Result<Vec<SearchResult>, Box<dyn std::error::Error>> {
    if k == 0 {
        return Ok(Vec::new());
    }

    let dataset = open_dataset(uri)?;
    let vector_dim = dataset_vector_dim(&dataset)?;
    if query.len() != vector_dim as usize {
        return Err(format!(
            "query dimension mismatch: expected {}, got {}",
            vector_dim,
            query.len()
        )
        .into());
    }

    let query_array = Float32Array::from(query);
    let query_ref: &dyn arrow::array::Array = &query_array;

    let mut scanner = dataset.scan();
    let has_revision = dataset.schema().field("revision").is_some();
    scanner.nearest("vector", query_ref, k)?;
    if has_revision {
        scanner.project(&["id", "label", "revision", "_distance"])?;
    } else {
        scanner.project(&["id", "label", "_distance"])?;
    }

    let stream = block_on(async { scanner.try_into_stream().await })?;
    let batches = collect_batches(&mut Box::pin(stream))?;

    let mut results = Vec::new();
    for batch in batches {
        check_for_interrupts!();
        append_vector_batch(&batch, &mut results)?;
    }
    results.sort_by(|a, b| {
        let distance_order = a
            .distance
            .partial_cmp(&b.distance)
            .unwrap_or(Ordering::Equal);
        distance_order.then_with(|| a.id.cmp(&b.id))
    });
    Ok(results)
}

/// Starts the Lance half of hybrid search on its existing Tokio worker. This
/// future contains no PostgreSQL API calls; SPI and interrupt checks stay on
/// the backend thread.
pub fn spawn_vector_search(uri: &str, query: Vec<f32>, k: usize) -> PendingVectorSearch {
    let uri = uri.to_string();
    PendingVectorSearch {
        task: runtime().spawn(async move { vector_search_async(uri, query, k).await }),
    }
}

/// Start an exact search over a bounded PostgreSQL eligibility set.
///
/// The id predicate is generated only from signed integers returned by the
/// parameterized PostgreSQL filter query. Prefiltering and disabling vector
/// indices ensure the `k = eligible_doc_ids.len()` scan returns every eligible
/// vector before the caller applies the final result limit.
pub fn spawn_filtered_vector_search(
    uri: &str,
    query: Vec<f32>,
    eligible_doc_ids: Vec<i64>,
) -> PendingVectorSearch {
    let uri = uri.to_string();
    PendingVectorSearch {
        task: runtime()
            .spawn(async move { vector_search_filtered_async(uri, query, eligible_doc_ids).await }),
    }
}

async fn vector_search_async(
    uri: String,
    query: Vec<f32>,
    k: usize,
) -> Result<Vec<SearchResult>, String> {
    if k == 0 {
        return Ok(Vec::new());
    }

    let dataset = DatasetBuilder::from_uri(&uri)
        .load()
        .await
        .map_err(|error| error.to_string())?;
    let vector_dim = dataset_vector_dim(&dataset).map_err(|error| error.to_string())?;
    if query.len() != vector_dim as usize {
        return Err(format!(
            "query dimension mismatch: expected {}, got {}",
            vector_dim,
            query.len()
        ));
    }

    let query_array = Float32Array::from(query);
    let query_ref: &dyn arrow::array::Array = &query_array;
    let mut scanner = dataset.scan();
    let has_revision = dataset.schema().field("revision").is_some();
    scanner
        .nearest("vector", query_ref, k)
        .map_err(|error| error.to_string())?;
    if has_revision {
        scanner
            .project(&["id", "label", "revision", "_distance"])
            .map_err(|error| error.to_string())?;
    } else {
        scanner
            .project(&["id", "label", "_distance"])
            .map_err(|error| error.to_string())?;
    }

    let mut stream = Box::pin(
        scanner
            .try_into_stream()
            .await
            .map_err(|error| error.to_string())?,
    );
    let mut results = Vec::new();
    while let Some(batch) = stream.next().await {
        let batch = batch.map_err(|error| error.to_string())?;
        append_vector_batch(&batch, &mut results).map_err(|error| error.to_string())?;
    }
    results.sort_by(|a, b| {
        let distance_order = a
            .distance
            .partial_cmp(&b.distance)
            .unwrap_or(Ordering::Equal);
        distance_order.then_with(|| a.id.cmp(&b.id))
    });
    Ok(results)
}

async fn vector_search_filtered_async(
    uri: String,
    query: Vec<f32>,
    eligible_doc_ids: Vec<i64>,
) -> Result<Vec<SearchResult>, String> {
    if eligible_doc_ids.is_empty() {
        return Ok(Vec::new());
    }

    let dataset = DatasetBuilder::from_uri(&uri)
        .load()
        .await
        .map_err(|error| error.to_string())?;
    let vector_dim = dataset_vector_dim(&dataset).map_err(|error| error.to_string())?;
    if query.len() != vector_dim as usize {
        return Err(format!(
            "query dimension mismatch: expected {}, got {}",
            vector_dim,
            query.len()
        ));
    }

    let id_filter = format!(
        "id IN ({})",
        eligible_doc_ids
            .iter()
            .map(i64::to_string)
            .collect::<Vec<_>>()
            .join(",")
    );
    let query_array = Float32Array::from(query);
    let query_ref: &dyn arrow::array::Array = &query_array;
    let mut scanner = dataset.scan();
    let has_revision = dataset.schema().field("revision").is_some();
    scanner
        .prefilter(true)
        .filter(&id_filter)
        .map_err(|error| error.to_string())?
        .nearest("vector", query_ref, eligible_doc_ids.len())
        .map_err(|error| error.to_string())?
        .use_index(false);
    if has_revision {
        scanner
            .project(&["id", "label", "revision", "_distance"])
            .map_err(|error| error.to_string())?;
    } else {
        scanner
            .project(&["id", "label", "_distance"])
            .map_err(|error| error.to_string())?;
    }

    let mut stream = Box::pin(
        scanner
            .try_into_stream()
            .await
            .map_err(|error| error.to_string())?,
    );
    let mut results = Vec::new();
    while let Some(batch) = stream.next().await {
        let batch = batch.map_err(|error| error.to_string())?;
        append_vector_batch(&batch, &mut results).map_err(|error| error.to_string())?;
    }
    results.sort_by(|a, b| {
        let distance_order = a
            .distance
            .partial_cmp(&b.distance)
            .unwrap_or(Ordering::Equal);
        distance_order.then_with(|| a.id.cmp(&b.id))
    });
    Ok(results)
}

fn append_vector_batch(
    batch: &RecordBatch,
    results: &mut Vec<SearchResult>,
) -> Result<(), Box<dyn std::error::Error>> {
    let schema = batch.schema();
    let id_index = schema.index_of("id")?;
    let label_index = schema.index_of("label")?;
    let distance_index = schema.index_of("_distance")?;
    let revision_index = schema.index_of("revision").ok();
    let id_array = batch
        .column(id_index)
        .as_any()
        .downcast_ref::<Int64Array>()
        .ok_or("id column not Int64")?;
    let label_array = batch
        .column(label_index)
        .as_any()
        .downcast_ref::<StringArray>()
        .ok_or("label column not Utf8")?;
    let dist_array = batch
        .column(distance_index)
        .as_any()
        .downcast_ref::<Float32Array>()
        .ok_or("_distance column not Float32")?;
    let revision_array = revision_index
        .map(|index| {
            batch
                .column(index)
                .as_any()
                .downcast_ref::<StringArray>()
                .ok_or("revision column not Utf8")
        })
        .transpose()?;

    for i in 0..batch.num_rows() {
        let label = if label_array.is_null(i) {
            None
        } else {
            Some(label_array.value(i).to_string())
        };
        results.push(SearchResult {
            id: id_array.value(i),
            label,
            revision: revision_array
                .and_then(|values| (!values.is_null(i)).then(|| values.value(i).to_string())),
            distance: dist_array.value(i),
        });
    }
    Ok(())
}

pub fn scan_rows(uri: &str, limit: i64) -> Result<Vec<ScanRow>, Box<dyn std::error::Error>> {
    if limit == 0 {
        return Ok(Vec::new());
    }
    let dataset = open_dataset(uri)?;

    let mut scanner = dataset.scan();
    scanner.project(&["id", "vector", "label"])?;
    if limit > 0 {
        scanner.limit(Some(limit), None)?;
    }

    let stream = block_on(async { scanner.try_into_stream().await })?;
    let batches = collect_batches(&mut Box::pin(stream))?;

    let mut rows = Vec::new();
    for batch in batches {
        check_for_interrupts!();
        let id_array = batch
            .column(0)
            .as_any()
            .downcast_ref::<Int64Array>()
            .ok_or("id column not Int64")?;
        let vector_array = batch
            .column(1)
            .as_any()
            .downcast_ref::<FixedSizeListArray>()
            .ok_or("vector column not FixedSizeList")?;
        let label_array = batch
            .column(2)
            .as_any()
            .downcast_ref::<StringArray>()
            .ok_or("label column not Utf8")?;

        for i in 0..batch.num_rows() {
            let row = vector_array.value(i);
            let floats = row
                .as_any()
                .downcast_ref::<Float32Array>()
                .ok_or("vector inner not Float32")?;
            let vec: Vec<f32> = floats.iter().map(|v| v.unwrap_or(0.0)).collect();

            let label = if label_array.is_null(i) {
                None
            } else {
                Some(label_array.value(i).to_string())
            };
            rows.push(ScanRow {
                id: id_array.value(i),
                vector: vec,
                label,
            });
        }
        if rows.len() as i64 >= limit {
            rows.truncate(limit as usize);
            break;
        }
    }
    Ok(rows)
}

/// Drains a Lance stream while checking for PostgreSQL interrupts between
/// batches instead of materializing everything before the first yield.
fn collect_batches<S>(stream: &mut S) -> Result<Vec<RecordBatch>, Box<dyn std::error::Error>>
where
    S: futures::Stream<Item = Result<RecordBatch, lance::Error>> + Unpin,
{
    let mut batches = Vec::new();
    loop {
        check_for_interrupts!();
        match block_on(stream.next()) {
            Some(Ok(batch)) => batches.push(batch),
            Some(Err(e)) => return Err(e.into()),
            None => return Ok(batches),
        }
    }
}
