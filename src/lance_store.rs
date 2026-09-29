use std::cmp::Ordering;
use std::sync::OnceLock;

use arrow::array::{
    Array, ArrayRef, FixedSizeListArray, Float32Array, Int64Array, RecordBatch,
    RecordBatchIterator, StringArray,
};
use arrow::datatypes::{DataType, Field, FieldRef, Schema};
use futures::StreamExt;
use lance::dataset::builder::DatasetBuilder;
use lance::dataset::{Dataset, WriteMode, WriteParams};
use std::sync::Arc;
use tokio::runtime::Runtime;

pub struct SearchResult {
    pub id: i64,
    pub label: Option<String>,
    pub distance: f32,
}

pub struct ScanRow {
    pub id: i64,
    pub vector: Vec<f32>,
    pub label: Option<String>,
}

fn runtime() -> &'static Runtime {
    static RT: OnceLock<Runtime> = OnceLock::new();
    RT.get_or_init(|| Runtime::new().expect("failed to create tokio runtime"))
}

fn block_on<F: std::future::Future>(f: F) -> F::Output {
    runtime().block_on(f)
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
    ]))
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
    vector_dim: i32,
) -> Result<RecordBatch, Box<dyn std::error::Error>> {
    if vector_dim <= 0 {
        return Err(format!("vector_dim must be positive: {vector_dim}").into());
    }
    if ids.len() != labels.len() {
        return Err(format!(
            "row count mismatch: {} ids but {} labels",
            ids.len(),
            labels.len()
        )
        .into());
    }
    let expected_values = ids.len() * vector_dim as usize;
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

    let batch = RecordBatch::try_new(
        schema(vector_dim),
        vec![
            Arc::new(id_array),
            Arc::new(vector_array),
            Arc::new(label_array),
        ],
    )?;
    Ok(batch)
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
    let batch = make_batch(vec![], vec![], vec![], vector_dim)?;
    let reader = RecordBatchIterator::new(vec![Ok(batch)].into_iter(), schema(vector_dim));
    let params = WriteParams {
        mode: if overwrite {
            WriteMode::Overwrite
        } else {
            WriteMode::Create
        },
        ..Default::default()
    };
    block_on(async { Dataset::write(reader, uri, Some(params)).await })?;
    Ok(())
}

pub fn insert_rows(
    uri: &str,
    ids: Vec<i64>,
    vectors: Vec<Vec<f32>>,
    labels: Vec<Option<String>>,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut dataset = block_on(async { DatasetBuilder::from_uri(uri).load().await })?;
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

    let batch = make_batch(ids, flat, labels, vector_dim)?;
    let reader = RecordBatchIterator::new(vec![Ok(batch)].into_iter(), schema(vector_dim));

    block_on(async { dataset.append(reader, None).await })?;
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
    let mut dataset = block_on(async { DatasetBuilder::from_uri(uri).load().await })?;
    let dataset_dim = dataset_vector_dim(&dataset)?;
    if vector_dim != dataset_dim {
        return Err(format!(
            "vector dimension mismatch: dataset expects {}, got {}",
            dataset_dim, vector_dim
        )
        .into());
    }

    let batch = make_batch(ids, flat_vectors, labels, vector_dim)?;
    let reader = RecordBatchIterator::new(vec![Ok(batch)].into_iter(), schema(vector_dim));

    block_on(async { dataset.append(reader, None).await })?;
    Ok(row_count)
}

pub fn count_rows(uri: &str) -> Result<usize, Box<dyn std::error::Error>> {
    let dataset = block_on(async { DatasetBuilder::from_uri(uri).load().await })?;
    let count = block_on(async { dataset.count_rows(None).await })?;
    Ok(count)
}

pub fn vector_dim(uri: &str) -> Result<i32, Box<dyn std::error::Error>> {
    let dataset = block_on(async { DatasetBuilder::from_uri(uri).load().await })?;
    dataset_vector_dim(&dataset)
}

pub fn vector_search(
    uri: &str,
    query: Vec<f32>,
    k: usize,
) -> Result<Vec<SearchResult>, Box<dyn std::error::Error>> {
    if k == 0 {
        return Ok(Vec::new());
    }

    let dataset = block_on(async { DatasetBuilder::from_uri(uri).load().await })?;
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
    scanner
        .nearest("vector", query_ref, k)?
        .project(&["id", "label", "_distance"])?;

    let stream = block_on(async { scanner.try_into_stream().await })?;
    let batches = block_on(async { stream.collect::<Vec<_>>().await })
        .into_iter()
        .collect::<Result<Vec<RecordBatch>, _>>()?;

    let mut results = Vec::new();
    for batch in batches {
        let id_array = batch
            .column(0)
            .as_any()
            .downcast_ref::<Int64Array>()
            .ok_or("id column not Int64")?;
        let label_array = batch
            .column(1)
            .as_any()
            .downcast_ref::<StringArray>()
            .ok_or("label column not Utf8")?;
        let dist_array = batch
            .column(2)
            .as_any()
            .downcast_ref::<Float32Array>()
            .ok_or("_distance column not Float32")?;

        for i in 0..batch.num_rows() {
            let label = if label_array.is_null(i) {
                None
            } else {
                Some(label_array.value(i).to_string())
            };
            results.push(SearchResult {
                id: id_array.value(i),
                label,
                distance: dist_array.value(i),
            });
        }
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

pub fn scan_rows(uri: &str, limit: i64) -> Result<Vec<ScanRow>, Box<dyn std::error::Error>> {
    if limit == 0 {
        return Ok(Vec::new());
    }
    let dataset = block_on(async { DatasetBuilder::from_uri(uri).load().await })?;

    let mut scanner = dataset.scan();
    scanner.project(&["id", "vector", "label"])?;
    if limit > 0 {
        scanner.limit(Some(limit), None)?;
    }

    let stream = block_on(async { scanner.try_into_stream().await })?;
    let batches = block_on(async { stream.collect::<Vec<_>>().await })
        .into_iter()
        .collect::<Result<Vec<RecordBatch>, _>>()?;

    let mut rows = Vec::new();
    for batch in batches {
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
    }
    Ok(rows)
}
