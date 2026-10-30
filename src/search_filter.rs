use pgrx::check_for_interrupts;
use pgrx::datum::DatumWithOid;
use pgrx::datum::JsonB;
use pgrx::pg_sys::panic::ErrorReportable;
use pgrx::prelude::PgSqlErrorCode;
use pgrx::spi::Spi;

use crate::{fail, MAX_FILTERED_DOC_IDS};

#[derive(Default)]
pub struct SearchFilters {
    domain: Option<String>,
    from: Option<String>,
    to: Option<String>,
    language: Option<String>,
    format: Option<String>,
    collection_id: Option<String>,
}

impl SearchFilters {
    pub fn parse(input: JsonB) -> Self {
        let object = input
            .0
            .as_object()
            .unwrap_or_else(|| invalid("filters must be a JSON object"));
        if object.is_empty() {
            invalid("at least one search filter is required");
        }
        if object.len() > 6 {
            invalid("filters contains more than 6 entries");
        }
        // Reject oversized values before serializing JSONB to measure the
        // overall input. This keeps validation's temporary allocation bounded
        // even when a caller supplies one very large string.
        for (key, value) in object {
            if !matches!(
                key.as_str(),
                "domain" | "from" | "to" | "language" | "format" | "collection_id"
            ) {
                invalid("filters contains an unsupported key");
            }
            let value = value
                .as_str()
                .unwrap_or_else(|| invalid("every search filter value must be a string"));
            if value.len() > 2048 {
                invalid("filters JSONB exceeds the 2048-byte limit");
            }
        }
        if input.0.to_string().len() > 2048 {
            invalid("filters JSONB exceeds the 2048-byte limit");
        }

        let mut filters = Self::default();
        for (key, value) in object {
            let value = match value.as_str() {
                Some(value) => value,
                None => invalid("every search filter value must be a string"),
            };
            match key.as_str() {
                "domain" => filters.domain = Some(normalize_domain(value)),
                "from" => filters.from = Some(validate_timestamp(value, "from")),
                "to" => filters.to = Some(validate_timestamp(value, "to")),
                "language" => filters.language = Some(normalize_language(value)),
                "format" => filters.format = Some(normalize_format(value)),
                "collection_id" => filters.collection_id = Some(validate_collection_id(value)),
                _ => invalid("filters contains an unsupported key"),
            }
        }

        if let (Some(from), Some(to)) = (&filters.from, &filters.to) {
            let ordered = Spi::get_one_with_args::<bool>(
                "SELECT $1::timestamptz <= $2::timestamptz",
                &[
                    DatumWithOid::from(from.clone()),
                    DatumWithOid::from(to.clone()),
                ],
            )
            .unwrap_or_report()
            .unwrap_or(false);
            if !ordered {
                invalid("from must be earlier than or equal to to");
            }
        }
        filters
    }

    pub fn matching_document_ids(&self) -> Vec<i64> {
        let mut predicates = Vec::with_capacity(6);
        let mut args = Vec::with_capacity(7);
        if let Some(value) = &self.domain {
            args.push(DatumWithOid::from(value.clone()));
            predicates.push(format!("domain = ${}::text", args.len()));
        }
        if let Some(value) = &self.from {
            args.push(DatumWithOid::from(value.clone()));
            predicates.push(format!("warc_date >= ${}::timestamptz", args.len()));
        }
        if let Some(value) = &self.to {
            args.push(DatumWithOid::from(value.clone()));
            predicates.push(format!("warc_date <= ${}::timestamptz", args.len()));
        }
        if let Some(value) = &self.language {
            args.push(DatumWithOid::from(value.clone()));
            predicates.push(format!(
                "string_to_array(content_language, ',') @> ARRAY[${}::text]",
                args.len()
            ));
        }
        if let Some(value) = &self.format {
            args.push(DatumWithOid::from(value.clone()));
            predicates.push(format!("media_type = ${}::text", args.len()));
        }
        if let Some(value) = &self.collection_id {
            args.push(DatumWithOid::from(value.clone()));
            predicates.push(format!("collection_id = ${}::text", args.len()));
        }
        args.push(DatumWithOid::from(MAX_FILTERED_DOC_IDS as i64 + 1));
        let sql = format!(
            "SELECT doc_id FROM pgwarc_lance.warc_record WHERE {} LIMIT ${}::bigint",
            predicates.join(" AND "),
            args.len()
        );
        let mut ids = Vec::new();
        Spi::connect(|client| {
            let rows = client.select(&sql, None, &args).unwrap_or_report();
            for row in rows {
                check_for_interrupts!();
                ids.push(
                    row.get_by_name::<i64, _>("doc_id")
                        .unwrap_or_report()
                        .unwrap(),
                );
            }
            Ok::<(), pgrx::spi::SpiError>(())
        })
        .unwrap_or_report();

        if ids.len() > MAX_FILTERED_DOC_IDS {
            fail(
                PgSqlErrorCode::ERRCODE_PROGRAM_LIMIT_EXCEEDED,
                &format!(
                    "search filters match more than {MAX_FILTERED_DOC_IDS} documents; narrow the filters"
                ),
            );
        }
        ids.sort_unstable();
        ids
    }
}

fn normalize_domain(value: &str) -> String {
    if value.is_empty() || value.len() > 253 || !value.is_ascii() {
        invalid("domain must be a normalized ASCII hostname of at most 253 bytes");
    }
    let domain = value.to_ascii_lowercase();
    if domain.contains(':') {
        if !domain
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() || byte == b':' || byte == b'.')
        {
            invalid("domain must be an exact hostname or IPv6 address");
        }
    } else {
        if domain.starts_with('.') || domain.ends_with('.') || domain.contains("..") {
            invalid("domain contains an empty hostname label");
        }
        for label in domain.split('.') {
            if label.is_empty()
                || label.len() > 63
                || label.starts_with('-')
                || label.ends_with('-')
                || !label
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
            {
                invalid("domain must be an exact DNS hostname");
            }
        }
    }
    domain
}

fn normalize_language(value: &str) -> String {
    if value.is_empty()
        || value.len() > 63
        || value.starts_with('-')
        || value.ends_with('-')
        || value.contains("--")
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
    {
        invalid("language must be one exact BCP 47 style tag of at most 63 bytes");
    }
    value.to_ascii_lowercase()
}

fn normalize_format(value: &str) -> String {
    let mut parts = value.split('/');
    let (Some(major), Some(minor), None) = (parts.next(), parts.next(), parts.next()) else {
        invalid("format must be one exact media type such as text/html");
    };
    let token = |part: &str| {
        !part.is_empty()
            && part.bytes().all(|byte| {
                byte.is_ascii_alphanumeric()
                    || matches!(
                        byte,
                        b'!' | b'#' | b'$' | b'&' | b'^' | b'_' | b'.' | b'+' | b'-'
                    )
            })
    };
    if value.len() > 127 || !token(major) || !token(minor) {
        invalid("format must be one exact media type of at most 127 bytes");
    }
    value.to_ascii_lowercase()
}

fn validate_timestamp(value: &str, name: &str) -> String {
    if value.len() > 64 || !value.contains('T') {
        invalid(&format!(
            "{name} must be an RFC 3339 timestamp with an explicit timezone"
        ));
    }
    let bytes = value.as_bytes();
    let has_timezone = value.ends_with('Z')
        || (bytes.len() >= 6
            && (bytes[bytes.len() - 6] == b'+' || bytes[bytes.len() - 6] == b'-')
            && bytes[bytes.len() - 3] == b':'
            && bytes[bytes.len() - 5..bytes.len() - 3]
                .iter()
                .all(u8::is_ascii_digit)
            && bytes[bytes.len() - 2..].iter().all(u8::is_ascii_digit));
    if !has_timezone {
        invalid(&format!("{name} must include Z or an explicit UTC offset"));
    }
    value.to_string()
}

fn validate_collection_id(value: &str) -> String {
    if value.is_empty()
        || value.len() > 96
        || !value.as_bytes()[0].is_ascii_alphanumeric()
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
    {
        invalid("collection_id must match the managed collection identifier format");
    }
    value.to_string()
}

fn invalid(message: &str) -> ! {
    fail(PgSqlErrorCode::ERRCODE_INVALID_PARAMETER_VALUE, message)
}

#[cfg(test)]
mod tests {
    use super::{normalize_domain, normalize_format, normalize_language};

    #[test]
    fn normalizes_exact_search_metadata_filters() {
        assert_eq!(normalize_domain("Example.Test"), "example.test");
        assert_eq!(normalize_language("EN-us"), "en-us");
        assert_eq!(normalize_format("Text/HTML"), "text/html");
    }
}
