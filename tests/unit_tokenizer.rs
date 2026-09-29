#[path = "../src/tokenizer.rs"]
mod tokenizer;

#[test]
fn korean_text_emits_bigrams() {
    let tokens = tokenizer::tokenize("안녕하세요");
    assert!(tokens.contains(&"안녕".to_string()));
    assert!(tokens.contains(&"녕하".to_string()));
}

#[test]
fn mixed_ascii_and_korean_text_is_tokenized() {
    let tokens = tokenizer::tokenize("PostgreSQL 검색");
    assert!(tokens.contains(&"postgresql".to_string()));
    assert!(tokens.contains(&"검색".to_string()));
}

#[test]
fn one_character_ascii_tokens_are_ignored() {
    let tokens = tokenizer::tokenize("a b cd");
    assert_eq!(tokens, vec!["cd".to_string()]);
}
