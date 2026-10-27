use std::sync::OnceLock;

/// The language variants supported by this compatibility package.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq, PartialOrd, Ord)]
pub enum Lang {
    /// English words from the bundled ENABLE word list.
    En,
}

static ENGLISH_WORDS: OnceLock<Vec<&'static str>> = OnceLock::new();

/// Returns the bundled words in their original order.
pub fn all(lang: Lang) -> &'static [&'static str] {
    match lang {
        Lang::En => ENGLISH_WORDS
            .get_or_init(|| include_str!("../words.txt").lines().collect())
            .as_slice(),
    }
}

#[cfg(test)]
mod tests {
    use super::{all, Lang};

    #[test]
    fn english_dataset_is_complete_and_stable() {
        let words = all(Lang::En);
        assert_eq!(words.len(), 178_187);
        assert!(words.iter().all(|word| !word.is_empty()));
        assert!(std::ptr::eq(words, all(Lang::En)));
    }
}
