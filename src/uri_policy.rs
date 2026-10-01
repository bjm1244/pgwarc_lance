//! Filesystem containment for a configured local Lance dataset root.
use std::fs;
use std::path::{Component, Path, PathBuf};

fn local_absolute_path(value: &str) -> Result<&Path, String> {
    let path = value.strip_prefix("file://").unwrap_or(value);
    if path.contains("://") || path.contains('%') || !Path::new(path).is_absolute() {
        return Err("restricted Lance URIs must be absolute local paths (or file:/// paths), without percent encoding".into());
    }
    let path = Path::new(path);
    if path.components().any(|c| c == Component::ParentDir) {
        return Err("parent traversal is not permitted in restricted Lance paths".into());
    }
    Ok(path)
}

/// Resolve existing symlinks even when the dataset or its parent does not exist
/// yet. A dangling symlink is an error, not a nonexistent directory to create.
fn resolve_for_create(path: &Path) -> Result<PathBuf, String> {
    let mut ancestor = path.to_path_buf();
    let mut missing = Vec::new();
    loop {
        match fs::symlink_metadata(&ancestor) {
            Ok(_) => {
                let mut resolved = fs::canonicalize(&ancestor)
                    .map_err(|e| format!("cannot resolve Lance path: {e}"))?;
                if !missing.is_empty() && !resolved.is_dir() {
                    return Err("existing Lance path ancestor must be a directory".into());
                }
                for part in missing.iter().rev() {
                    resolved.push(part);
                }
                return Ok(resolved);
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                let part = ancestor
                    .file_name()
                    .ok_or("cannot resolve Lance path ancestor")?;
                missing.push(part.to_os_string());
                if !ancestor.pop() {
                    return Err("cannot resolve Lance path ancestor".into());
                }
            }
            Err(e) => return Err(format!("cannot inspect Lance path: {e}")),
        }
    }
}

/// Returns the resolved path used for both I/O and the writer lock. Operators
/// must prevent untrusted OS users from replacing directories during a call.
pub fn restricted_path(root: &str, uri: &str) -> Result<String, String> {
    let root = fs::canonicalize(local_absolute_path(root)?)
        .map_err(|e| format!("allowed_uri_prefix must be an existing local directory: {e}"))?;
    if !root.is_dir() {
        return Err("allowed_uri_prefix must be a directory".into());
    }
    let candidate = resolve_for_create(local_absolute_path(uri)?)?;
    if !candidate.starts_with(&root) {
        return Err("Lance dataset path is outside allowed_uri_prefix".into());
    }
    candidate
        .into_os_string()
        .into_string()
        .map_err(|_| "Lance dataset path must be valid UTF-8".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            let path = std::env::temp_dir().join(format!(
                "pgwarc-uri-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir_all(path.join("allowed")).unwrap();
            fs::create_dir_all(path.join("outside")).unwrap();
            Self(path)
        }
        fn root(&self) -> String {
            self.0.join("allowed").to_str().unwrap().into()
        }
        fn uri(&self, path: &str) -> String {
            self.0.join(path).to_str().unwrap().into()
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.0).unwrap();
        }
    }

    #[test]
    fn allows_new_nested_dataset_and_normalizes_file_uri() {
        let f = Fixture::new();
        let path = f.uri("allowed/new/dataset.lance");
        let resolved = restricted_path(&f.root(), &path).unwrap();
        assert_eq!(
            restricted_path(&f.root(), &format!("file://{path}")).unwrap(),
            resolved
        );
        assert!(Path::new(&resolved).starts_with(fs::canonicalize(f.root()).unwrap()));
    }

    #[test]
    fn rejects_sibling_prefix_and_parent_traversal() {
        let f = Fixture::new();
        assert!(restricted_path(&f.root(), &f.uri("allowed-sibling/dataset.lance")).is_err());
        assert!(restricted_path(&f.root(), &f.uri("allowed/../outside/dataset.lance")).is_err());
        assert!(
            restricted_path(&f.root(), &f.uri("allowed/new/../../outside/dataset.lance")).is_err()
        );
    }

    #[test]
    fn rejects_nonlocal_relative_and_encoded_paths() {
        let f = Fixture::new();
        for uri in [
            "relative.lance",
            "s3://bucket/dataset",
            "file://localhost/tmp/dataset",
            "file:///tmp/%2e%2e/dataset",
        ] {
            assert!(restricted_path(&f.root(), uri).is_err(), "accepted {uri}");
        }
    }

    #[test]
    fn rejects_missing_root_and_file_ancestor() {
        let f = Fixture::new();
        assert!(restricted_path(&f.uri("missing"), &f.uri("missing/dataset")).is_err());
        fs::write(f.uri("allowed/file"), "data").unwrap();
        assert!(restricted_path(&f.root(), &f.uri("allowed/file/dataset")).is_err());
        assert!(restricted_path(&f.uri("allowed/file"), &f.uri("allowed/file")).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn resolves_internal_alias_but_rejects_escaping_and_dangling_symlinks() {
        use std::os::unix::fs::symlink;
        let f = Fixture::new();
        fs::create_dir(f.uri("allowed/real")).unwrap();
        symlink(f.uri("allowed/real"), f.uri("allowed/alias")).unwrap();
        symlink(f.uri("outside"), f.uri("allowed/escape")).unwrap();
        symlink(f.uri("nonexistent"), f.uri("allowed/dangling")).unwrap();
        assert_eq!(
            restricted_path(&f.root(), &f.uri("allowed/alias/new.lance")).unwrap(),
            restricted_path(&f.root(), &f.uri("allowed/real/new.lance")).unwrap()
        );
        assert!(restricted_path(&f.root(), &f.uri("allowed/escape/new.lance")).is_err());
        assert!(restricted_path(&f.root(), &f.uri("allowed/dangling/new.lance")).is_err());
    }
}
