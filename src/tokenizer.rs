pub fn tokenize(text: &str) -> Vec<String> {
    let lower: Vec<char> = text.chars().map(|c| c.to_ascii_lowercase()).collect();
    let mut tokens: Vec<String> = Vec::new();

    let mut i = 0;
    while i < lower.len() {
        let c = lower[i];
        if is_cjk(c) {
            let start = i;
            while i < lower.len() && is_cjk(lower[i]) {
                i += 1;
            }
            let run: String = lower[start..i].iter().collect();
            emit_cjk_tokens(&run, &mut tokens);
        } else if c.is_ascii_alphanumeric() {
            let start = i;
            while i < lower.len() && lower[i].is_ascii_alphanumeric() {
                i += 1;
            }
            let word: String = lower[start..i].iter().collect();
            if word.len() >= 2 {
                tokens.push(word);
            }
        } else {
            i += 1;
        }
    }
    tokens
}

fn is_cjk(c: char) -> bool {
    matches!(c,
        '\u{AC00}'..='\u{D7A3}'
        | '\u{1100}'..='\u{11FF}'
        | '\u{3130}'..='\u{318F}'
        | '\u{4E00}'..='\u{9FFF}'
        | '\u{3400}'..='\u{4DBF}'
        | '\u{3040}'..='\u{30FF}'
        | '\u{F900}'..='\u{FAFF}'
    )
}

fn emit_cjk_tokens(run: &str, tokens: &mut Vec<String>) {
    let chars: Vec<char> = run.chars().collect();
    if chars.is_empty() {
        return;
    }
    if chars.len() == 1 {
        tokens.push(chars[0].to_string());
        return;
    }
    for w in chars.windows(2) {
        tokens.push(format!("{}{}", w[0], w[1]));
    }
}
