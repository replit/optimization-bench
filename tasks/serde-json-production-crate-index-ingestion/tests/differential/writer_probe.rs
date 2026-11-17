// Verifier-only reference adapter: public serialization APIs + std::io::Write faults.
// The sink obeys Write's contract: bytes are accepted only on Ok(n), never on Err.
use serde::Serialize;
use serde_json::Value;
use std::io::{self, Read, Write};
use std::panic::{catch_unwind, AssertUnwindSafe};

struct Sink {
    bytes: Vec<u8>,
    max_chunk: usize,
    fail_after: Option<usize>,
    zero_after: Option<usize>,
    interrupt_at: Option<usize>,
    interrupted: bool,
    kind: io::ErrorKind,
    calls: Vec<String>,
    call_count: usize,
    flush_error: bool,
}

impl Sink {
    fn record(&mut self, value: String) {
        self.call_count += 1;
        if self.calls.len() < 256 { self.calls.push(value); }
    }
}

impl Write for Sink {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        if buf.is_empty() { return Ok(0); }
        if !self.interrupted && self.interrupt_at.is_some_and(|n| self.bytes.len() >= n) {
            self.interrupted = true;
            self.record(format!("{{\"requested\":{},\"error\":\"Interrupted\"}}",buf.len()));
            return Err(io::Error::from(io::ErrorKind::Interrupted));
        }
        if self.fail_after.is_some_and(|n| self.bytes.len() >= n) {
            self.record(format!("{{\"requested\":{},\"error\":\"{:?}\"}}",buf.len(),self.kind));
            return Err(io::Error::from(self.kind));
        }
        if self.zero_after.is_some_and(|n| self.bytes.len() >= n) {
            self.record(format!("{{\"requested\":{},\"accepted\":0}}",buf.len()));
            return Ok(0);
        }
        let mut n = buf.len().min(self.max_chunk);
        if let Some(limit) = self.fail_after { n = n.min(limit - self.bytes.len()); }
        if let Some(limit) = self.zero_after { n = n.min(limit - self.bytes.len()); }
        self.bytes.extend_from_slice(&buf[..n]);
        self.record(format!("{{\"requested\":{},\"accepted\":{}}}",buf.len(),n));
        Ok(n)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.record("{\"flush\":true}".to_owned());
        if self.flush_error { Err(io::Error::from(self.kind)) } else { Ok(()) }
    }
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{:02x}",b)).collect()
}

fn observe(api: &str, case: &Value) -> String {
    let option = |key: &str| case[key].as_u64().map(|n| n as usize);
    let mut sink = Sink {
        bytes: Vec::new(), max_chunk: option("max_chunk").unwrap_or(usize::MAX).max(1),
        fail_after: option("fail_after"), zero_after: option("zero_after"),
        interrupt_at: option("interrupt_at"), interrupted:false,
        kind: match case["error_kind"].as_str().unwrap_or("Other") {
            "BrokenPipe" => io::ErrorKind::BrokenPipe,
            "PermissionDenied" => io::ErrorKind::PermissionDenied,
            "Other" => io::ErrorKind::Other,
            _ => panic!("invalid verifier fault kind"),
        },
        calls:Vec::new(), call_count:0, flush_error:case["flush_error"].as_bool().unwrap_or(false),
    };
    let value = &case["value"];
    let returned = catch_unwind(AssertUnwindSafe(|| match api {
        "serde_json::to_writer" => serde_json::to_writer(&mut sink, value),
        "serde_json::to_writer_pretty" => serde_json::to_writer_pretty(&mut sink, value),
        "serde_json::Serializer::new" => value.serialize(&mut serde_json::Serializer::new(&mut sink)),
        "serde_json::Serializer::pretty" => value.serialize(&mut serde_json::Serializer::pretty(&mut sink)),
        _ => panic!("invalid verifier public surface"),
    }));
    let outcome = match returned {
        Ok(Ok(())) => "{\"termination\":\"return\",\"result\":\"ok\"}".to_owned(),
        Ok(Err(error)) => format!(
            "{{\"termination\":\"return\",\"result\":\"error\",\"category\":\"{:?}\",\"io_kind\":{}}}",
            error.classify(),error.io_error_kind().map(|k|format!("\"{:?}\"",k)).unwrap_or("null".to_owned())),
        Err(_) => "{\"termination\":\"panic\"}".to_owned(),
    };
    // Independent observation channel: never serialize the report using the
    // candidate's to_writer/to_string/Value Display implementation.
    format!("{{\"surface\":\"{}\",\"outcome\":{},\"written_hex\":\"{}\",\
        \"diagnostics\":{{\"calls\":[{}],\"call_count\":{},\"interrupted\":{},\"calls_truncated\":{}}}}}",
        api,outcome,hex(&sink.bytes),sink.calls.join(","),sink.call_count,sink.interrupted,sink.call_count>256)
}

fn main() {
    // Panic messages/source paths are not contractual. Presence of panic IS.
    std::panic::set_hook(Box::new(|_| {}));
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let case: Value = serde_json::from_str(&input).unwrap();
    let apis = ["serde_json::to_writer","serde_json::to_writer_pretty",
                "serde_json::Serializer::new","serde_json::Serializer::pretty"];
    println!("{{\"format\":\"optimbench-writer-observations-v1\",\"observations\":[{}]}}",
        apis.iter().map(|api|observe(api,&case)).collect::<Vec<_>>().join(","));
}
