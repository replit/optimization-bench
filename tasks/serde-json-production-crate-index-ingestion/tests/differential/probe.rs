// Verifier-only public API probe. Never copied into an agent context.
use std::io::{self, Read};
use serde_json::{json, Value};

fn outcome(raw: &[u8]) -> Value {
    match serde_json::from_slice::<Value>(raw) {
        Ok(value) => json!({"ok":true,"serialized":serde_json::to_string(&value).unwrap(),
            "roundtrip":value}),
        Err(err) => json!({"ok":false,"category":format!("{:?}",err.classify()),
            "line":err.line(),"column":err.column(),"message":err.to_string()}),
    }
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let case: Value = serde_json::from_str(&input).unwrap();
    let raw: Vec<u8> = case["hex"].as_str().unwrap().as_bytes().chunks(2)
        .map(|b| u8::from_str_radix(std::str::from_utf8(b).unwrap(),16).unwrap()).collect();
    let mut answers = vec![];
    for _ in 0..case["repeat"].as_u64().unwrap_or(1) {
        let result = outcome(&raw);
        let mut stream = serde_json::Deserializer::from_slice(&raw).into_iter::<Value>();
        let mut values = vec![];
        while let Some(item) = stream.next() {
            match item {
                Ok(value) => values.push(json!({"ok":value})),
                Err(err) => {
                    values.push(json!({"error":err.to_string(),"line":err.line(),
                        "column":err.column(),"category":format!("{:?}",err.classify())}));
                    break;
                }
            }
            if values.len()>4096 { panic!("bounded verifier stream exceeded"); }
        }
        let mut a: Value = serde_json::from_str(r#"{"x":[1,2],"path":{"~1":true}}"#).unwrap();
        let b: Value = serde_json::from_str(r#"{"unchanged":[true,null,"λ"]}"#).unwrap();
        let before = serde_json::to_string(&b).unwrap();
        a["x"][0] = json!({"mutation":"🙂"});
        let after = serde_json::to_string(&b).unwrap();
        let pointer = a.pointer("/path/~01").cloned();
        let borrowed = match std::str::from_utf8(&raw) {
            Ok(text) => match serde_json::from_str::<&str>(text) {
                Ok(s) => json!({"ok":s}),
                Err(e) => json!({"error":e.to_string(),"line":e.line(),"column":e.column()}),
            },
            Err(_) => json!({"utf8":"invalid"}),
        };
        let numbers = [
            serde_json::to_string(&i128::MIN).unwrap(),
            serde_json::to_string(&u128::MAX).unwrap(),
            serde_json::to_string(&-0.0f64).unwrap(),
        ];
        answers.push(json!({"result":result,"stream":values,"stream_offset":stream.byte_offset(),
            "mutated":a,"independent_before":before,"independent_after":after,
            "pointer":pointer,"borrowed":borrowed,"numbers":numbers}));
    }
    println!("{}", serde_json::to_string(&answers).unwrap());
}
