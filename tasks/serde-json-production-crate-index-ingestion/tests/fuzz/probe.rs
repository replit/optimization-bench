// Verifier-only. The input tape and observation reporter do NOT use candidate
// parsing/serialization. Only the explicitly selected public API is under test.
use serde::Serialize;
use serde_json::Value;
use std::cell::Cell;
use std::io::{self, Read, Write};
use std::panic::{catch_unwind, AssertUnwindSafe};

// Observed inside the real fresh/persistent library worker, not copied from
// Docker flags or the Python parent. No dependency on candidate serde code.
fn execution_affinity() -> String {
    extern "C" {
        fn sched_getaffinity(pid: i32, size: usize, mask: *mut usize) -> i32;
    }
    let mut mask = [0usize; 128];
    if unsafe { sched_getaffinity(0, std::mem::size_of_val(&mask), mask.as_mut_ptr()) } != 0 {
        return "null".into();
    }
    let cpus: Vec<String> = (0..mask.len() * usize::BITS as usize)
        .filter(|cpu| mask[cpu / usize::BITS as usize] & (1usize << (cpu % usize::BITS as usize)) != 0)
        .map(|cpu| cpu.to_string()).collect();
    format!("[{}]", cpus.join(","))
}

struct Tape<'a> { bytes: &'a [u8], at: usize }
impl<'a> Tape<'a> {
    fn take(&mut self, n: usize) -> &'a [u8] {
        let end = self.at.checked_add(n).expect("verifier tape overflow");
        let out = self.bytes.get(self.at..end).expect("verifier truncated tape");
        self.at = end; out
    }
    fn byte(&mut self) -> u8 { self.take(1)[0] }
    fn u32(&mut self) -> usize { u32::from_le_bytes(self.take(4).try_into().unwrap()) as usize }
    fn value(&mut self, depth: usize) -> Value {
        assert!(depth <= 64, "verifier tape depth");
        match self.byte() {
            b'n' => Value::Null,
            b't' => Value::Bool(true),
            b'f' => Value::Bool(false),
            b'i' => Value::from(i64::from_le_bytes(self.take(8).try_into().unwrap())),
            b's' => { let n = self.u32(); Value::String(String::from_utf8(self.take(n).to_vec()).unwrap()) },
            b'a' => { let n = self.u32(); Value::Array((0..n).map(|_| self.value(depth+1)).collect()) },
            b'o' => {
                let n = self.u32(); let mut map = serde_json::Map::new();
                for _ in 0..n {
                    let key = match self.value(depth+1) { Value::String(s) => s, _ => panic!("verifier nonstring key") };
                    assert!(map.insert(key, self.value(depth+1)).is_none(), "verifier duplicate key");
                }
                Value::Object(map)
            },
            _ => panic!("verifier unknown value tag"),
        }
    }
}
fn hex(bytes: &[u8]) -> String { bytes.iter().map(|b| format!("{b:02x}")).collect() }
// Canonical semantic tape for arbitrary candidate-produced Values. No Value's
// Display, candidate to_string or Serialize implementation participates.
fn value_tape(value: &Value, out: &mut Vec<u8>) {
    let sized = |tag: u8, data: &[u8], out: &mut Vec<u8>| {
        out.push(tag); out.extend_from_slice(&(data.len() as u32).to_le_bytes()); out.extend_from_slice(data);
    };
    match value {
        Value::Null => out.push(b'n'),
        Value::Bool(b) => out.push(if *b { b't' } else { b'f' }),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() { out.push(b'i'); out.extend_from_slice(&i.to_le_bytes()); }
            else if let Some(u) = n.as_u64() { out.push(b'u'); out.extend_from_slice(&u.to_le_bytes()); }
            else { out.push(b'd'); out.extend_from_slice(&n.as_f64().unwrap().to_bits().to_le_bytes()); }
        },
        Value::String(s) => sized(b's', s.as_bytes(), out),
        Value::Array(a) => {
            out.push(b'a'); out.extend_from_slice(&(a.len() as u32).to_le_bytes());
            for v in a { value_tape(v, out); }
        },
        Value::Object(m) => {
            out.push(b'o'); out.extend_from_slice(&(m.len() as u32).to_le_bytes());
            let mut keys: Vec<_> = m.keys().collect(); keys.sort();
            for k in keys { sized(b's', k.as_bytes(), out); value_tape(&m[k], out); }
        },
    }
}
struct FaultIo {
    data: Vec<u8>, at: usize, mode: u8, chunk: usize, threshold: usize,
    kind: io::ErrorKind, interrupted: bool, calls: usize,
    fault_fired: bool,
}
impl FaultIo {
    fn allowance(&mut self, requested: usize) -> io::Result<usize> {
        self.calls += 1;
        if requested == 0 { return Ok(0); }
        if self.mode == 4 && !self.interrupted && self.at >= self.threshold {
            self.interrupted = true; return Err(io::Error::from(io::ErrorKind::Interrupted));
        }
        if self.mode == 2 && self.at >= self.threshold {
            self.fault_fired=true; return Err(io::Error::from(self.kind));
        }
        if self.mode == 3 && self.at >= self.threshold { return Ok(0); }
        let mut n = requested;
        if self.mode == 1 { n = n.min(self.chunk); }
        if self.mode == 2 || self.mode == 3 { n = n.min(self.threshold - self.at); }
        if self.mode == 4 && !self.interrupted && self.at < self.threshold {
            n = n.min(self.threshold - self.at);
        }
        Ok(n)
    }
}
impl Write for FaultIo {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let n = self.allowance(bytes.len())?;
        self.data.extend_from_slice(&bytes[..n]); self.at += n; Ok(n)
    }
    fn flush(&mut self) -> io::Result<()> { Ok(()) }
}
impl Read for FaultIo {
    fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
        let n = self.allowance(out.len())?.min(self.data.len() - self.at);
        out[..n].copy_from_slice(&self.data[self.at..self.at+n]); self.at += n; Ok(n)
    }
}
struct SerializeThenPanic<'a> { entered: &'a Cell<bool>, continued: &'a Cell<bool> }
impl Serialize for SerializeThenPanic<'_> {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        self.entered.set(true);
        let _ = serializer.serialize_str("value")?;
        self.continued.set(true);
        panic!("must not be reached when first write fails")
    }
}
fn error_json(error: &serde_json::Error) -> String {
    format!("{{\"kind\":\"error\",\"category\":\"{:?}\",\"io_kind\":{},\"line\":{},\"column\":{}}}",
        error.classify(), error.io_error_kind().map(|k| format!("\"{k:?}\"")).unwrap_or("null".into()),
        error.line(), error.column())
}
fn process_ids(path: &str) -> Vec<String> {
    let mut ids: Vec<String> = std::fs::read_dir(path).unwrap()
        .map(|entry| entry.unwrap().file_name().into_string().unwrap()).collect();
    ids.sort(); ids
}
fn descendants() -> String {
    // Kernel builds may omit /proc/<tid>/children. This verifier-only fallback
    // reads numeric PID metadata, never cmdline, environment or process memory.
    let listing = match std::fs::read_dir("/proc") {
        Ok(entries) => entries,
        Err(_) => return "{\"available\":false,\"source\":\"proc-stat-ppid-starttime\"}".into(),
    };
    let mut records = Vec::new();
    let mut unreadable = 0usize;
    for entry in listing {
        let entry = match entry { Ok(e) => e, Err(_) => { unreadable+=1; continue; } };
        let pid = match entry.file_name().to_str().and_then(|s| s.parse::<u32>().ok()) {
            Some(pid) => pid, None => continue,
        };
        let stat = match std::fs::read_to_string(entry.path().join("stat")) {
            Ok(text) => text, Err(_) => { unreadable+=1; continue; },
        };
        let tail: Vec<_> = match stat.rsplit_once(')') {
            Some((_, tail)) => tail.split_whitespace().collect(),
            None => { unreadable+=1; continue; },
        };
        if tail.len() < 20 { unreadable+=1; continue; }
        let parent = match tail[1].parse::<u32>() { Ok(n) => n, Err(_) => { unreadable+=1; continue; } };
        let start = match tail[19].parse::<u64>() { Ok(n) => n, Err(_) => { unreadable+=1; continue; } };
        records.push((pid,parent,start,tail[0].to_owned()));
    }
    let mut family = vec![std::process::id()];
    loop {
        let old = family.len();
        for &(pid,parent,_,_) in &records {
            if family.contains(&parent) && !family.contains(&pid) { family.push(pid); }
        }
        if old == family.len() { break; }
    }
    let mut found: Vec<_> = records.iter().filter(|(pid,_,_,_)|
        *pid != std::process::id() && family.contains(pid)).collect();
    found.sort_by_key(|row|row.0);
    format!("{{\"available\":{},\"source\":\"proc-stat-ppid-starttime\",\"unreadable_pid_records\":{},\"descendants\":[{}]}}",
        unreadable==0,unreadable,found.iter().map(|(pid,parent,start,state)|
            format!("{{\"pid\":{pid},\"ppid\":{parent},\"starttime\":{start},\"state\":\"{state}\"}}")).collect::<Vec<_>>().join(","))
}
fn lifecycle(phase: &str) -> String {
    // read_dir's temporary FD is present in both snapshots. No process exit is
    // substituted for per-public-call lifecycle evidence.
    let mut threads = Vec::new();
    let mut unavailable = false;
    for tid in process_ids("/proc/self/task") {
        match std::fs::read_to_string(format!("/proc/self/task/{tid}/stat")) {
            Ok(stat) => {
                let fields: Vec<_> = stat.rsplit_once(')').unwrap().1.split_whitespace().collect();
                if fields.len() < 20 { unavailable=true; continue; }
                if matches!(fields[0], "Z" | "X" | "x") { continue; }
                threads.push(format!("{{\"tid\":{tid},\"start_ticks\":{},\"tgid\":{},\
                    \"scope_pid\":{},\"state\":\"{}\"}}",fields[19],std::process::id(),std::process::id(),fields[0]));
            },
            Err(e) if e.kind()==io::ErrorKind::NotFound => {},
            Err(_) => unavailable=true,
        }
    }
    let fds = process_ids("/proc/self/fd");
    let children = std::fs::read_to_string(format!("/proc/self/task/{}/children",std::process::id()));
    let child_ids = match children {
        Ok(text) => format!("[{}]",text.split_whitespace().map(|s|format!("\"{s}\"")).collect::<Vec<_>>().join(",")),
        Err(_) => "null".to_owned(),
    };
    let fallback = descendants();
    let clock = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    format!("{{\"thread_identities\":[{}],\"threads\":{},\"thread_identity_available\":{},\
        \"phase\":\"{phase}\",\"sample_clock_unix_ns\":\"{clock}\",\
        \"raw_method\":\"proc-task-stat-field22; Z/X excluded; no name whitelist\",\
        \"fd_ids\":[{}],\"child_ids\":{child_ids},\
        \"child_inventory_source\":\"proc-task-children; null means unavailable\",\
        \"descendant_inventory\":{fallback}}}",
        threads.join(","),threads.len(),!unavailable,
        fds.iter().map(|s| format!("\"{s}\"")).collect::<Vec<_>>().join(","))
}
fn alias_effects(payload: &[u8]) -> Result<String, serde_json::Error> {
    let mut left: Value = serde_json::from_slice(payload)?;
    let right: Value = serde_json::from_slice(&payload[..])?;
    let retained = left.clone();
    let borrowed: std::collections::BTreeMap<&str, Value> = serde_json::from_slice(payload)?;
    left["array"].as_array_mut().unwrap().push(Value::Bool(false));
    left["object"]["alias-mutation"] = Value::String("owned-only".into());
    let mut results = Vec::new();
    for (name, value) in [("mutated", &left), ("independent", &right), ("retained", &retained)] {
        let mut bytes = Vec::new();
        value_tape(value, &mut bytes);
        results.push(format!("\"{name}_tape_hex\":\"{}\"", hex(&bytes)));
    }
    let mut entries = Vec::new();
    for (key, value) in borrowed {
        let mut bytes = Vec::new();
        value_tape(&value, &mut bytes);
        entries.push(format!("{{\"key_hex\":\"{}\",\"value_tape_hex\":\"{}\"}}",
                             hex(key.as_bytes()), hex(&bytes)));
    }
    results.push(format!("\"borrowed_entries\":[{}]", entries.join(",")));
    results.push(format!("\"shared_input_hex\":\"{}\"", hex(payload)));
    Ok(results.join(","))
}

fn observe(raw: &[u8]) -> String {
    let mut tape = Tape {bytes: &raw, at:0};
    assert_eq!(tape.take(4), b"SJF1");
    let api = tape.byte(); let mode = tape.byte();
    let kind = match tape.byte() { 0 => io::ErrorKind::Other, 1 => io::ErrorKind::BrokenPipe,
        2 => io::ErrorKind::PermissionDenied, _ => panic!("verifier error kind") };
    let chunk = tape.u32(); let threshold = tape.u32(); let flags = tape.byte();
    let n = tape.u32(); let payload = tape.take(n);
    assert_eq!(tape.at, raw.len()); assert!(api < 8 && mode < 5 && chunk > 0 && flags < 3);
    assert!(flags != 2 || (api == 4 && mode == 0));
    let mut io = FaultIo {data: if api < 4 {vec![]} else {payload.to_vec()},
        at:0, mode, chunk, threshold, kind, interrupted:false, calls:0, fault_fired:false};
    let value = if api < 4 {
        let mut t = Tape {bytes:payload, at:0};
        let value = t.value(0); assert_eq!(t.at, payload.len()); Some(value)
    } else { None };
    let mut effects = String::new();
    let mut stream_values = Vec::new();
    let callback_entered = Cell::new(false);
    let callback_continued = Cell::new(false);
    let before = lifecycle("before");
    let returned = catch_unwind(AssertUnwindSafe(|| -> Result<(), serde_json::Error> {
        match api {
            0 if flags == 1 => serde_json::to_writer(&mut io, &SerializeThenPanic {
                entered:&callback_entered, continued:&callback_continued}),
            0 => serde_json::to_writer(&mut io, value.as_ref().unwrap()),
            1 => serde_json::to_writer_pretty(&mut io, value.as_ref().unwrap()),
            2 => value.as_ref().unwrap().serialize(&mut serde_json::Serializer::new(&mut io)),
            3 => value.as_ref().unwrap().serialize(&mut serde_json::Serializer::pretty(&mut io)),
            4 if flags == 2 => alias_effects(payload).map(|result| effects = result),
            4 | 5 | 6 => {
                let result: Result<Value, _> = match api {
                    4 => serde_json::from_slice(payload),
                    5 => serde_json::from_str(std::str::from_utf8(payload).expect("verifier &str UTF-8")),
                    _ => serde_json::from_reader(&mut io),
                };
                result.map(|v| { let mut bytes=vec![]; value_tape(&v, &mut bytes);
                    effects=format!("\"value_tape_hex\":\"{}\"",hex(&bytes)); })
            },
            7 => {
                let mut stream = serde_json::Deserializer::from_reader(&mut io).into_iter::<Value>();
                let mut failure = None;
                while let Some(next) = stream.next() {
                    match next {
                        Ok(v) => { let mut bytes=vec![]; value_tape(&v,&mut bytes); stream_values.push(hex(&bytes)); },
                        Err(e) => { failure=Some(e); break; },
                    }
                }
                effects = format!("\"value_tapes_hex\":[{}],\"byte_offset\":{}",
                    stream_values.iter().map(|s| format!("\"{s}\"")).collect::<Vec<_>>().join(","),
                    stream.byte_offset());
                match failure { Some(e) => Err(e), None => Ok(()) }
            },
            _ => unreachable!(),
        }
    }));
    let after = lifecycle("after");
    let (termination, outcome) = match returned {
        Ok(Ok(())) => ("return", "{\"kind\":\"ok\",\"value\":null}".to_owned()),
        Ok(Err(e)) => ("return", error_json(&e)),
        Err(_) => ("panic", "{\"kind\":\"error\",\"category\":\"panic\",\"io_kind\":null,\"line\":null,\"column\":null}".to_owned()),
    };
    if api < 4 { effects=format!("\"accepted_bytes_hex\":\"{}\"",hex(&io.data)); }
    if flags == 1 {
        effects.push_str(&format!(",\"serialize_callback_entered\":{},\"serialize_callback_continued\":{}",
            callback_entered.get(),callback_continued.get()));
    }
    // Input read-ahead and write chunking are deliberately diagnostics.
    let affinity = execution_affinity();
    format!("{{\"termination\":{{\"kind\":\"{termination}\",\"exit_code\":0}},\"outcome\":{outcome},\
        \"effects\":{{{effects}}},\"diagnostics\":{{\"calls\":{},\"io_position\":{},\"interrupted\":{},\
        \"io_error_fault_fired\":{},\"execution_affinity\":{affinity},\
        \"lifecycle_before\":{before},\"lifecycle_after\":{after}}}}}",
        io.calls,io.at,io.interrupted,io.fault_fired)
}
fn main() {
    if std::env::var_os("OPTIMBENCH_VERIFIER_DEBUG").is_none() {
        std::panic::set_hook(Box::new(|_| {}));
    }
    if std::env::args().any(|arg| arg == "--batch") {
        let mut stdin = io::stdin().lock();
        let mut stdout = io::stdout().lock();
        loop {
            let mut size = [0u8;4];
            match stdin.read_exact(&mut size) {
                Ok(()) => {},
                Err(e) if e.kind() == io::ErrorKind::UnexpectedEof => break,
                Err(e) => panic!("verifier batch transport: {e}"),
            }
            let n = u32::from_le_bytes(size) as usize;
            assert!(n <= 2*1024*1024, "verifier batch frame bound");
            let mut frame = vec![0u8;n]; stdin.read_exact(&mut frame).unwrap();
            writeln!(stdout,"{}",observe(&frame)).unwrap(); stdout.flush().unwrap();
        }
    } else {
        let mut raw = Vec::new(); io::stdin().read_to_end(&mut raw).unwrap();
        println!("{}", observe(&raw));
    }
}
