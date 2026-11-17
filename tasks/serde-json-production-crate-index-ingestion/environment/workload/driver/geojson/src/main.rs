// Protected geojson-typed resident driver (second performance unit). Library
// source/deps are unchanged. Like the registry driver, it pre-reads every
// DISTINCT complete file and sizes/touches one owned output buffer per file
// before the ready stop. Between stops: serde_json::Deserializer::from_slice,
// the typed RFC 7946 DeserializeSeed into that owned buffer, and end() -- the
// exact serde_json::from_slice code path. Disk reads and export are untimed.
mod flat;

use std::path::Path;
unsafe extern "C" { fn raise(signal: i32) -> i32; }
fn stop() { assert_eq!(unsafe { raise(19) }, 0, "resident SIGSTOP"); }

fn batch(list: &Path, output: &Path, resident: bool) -> Result<(), Box<dyn std::error::Error>> {
    let manifest = std::fs::read_to_string(list)?;
    let mut names = std::collections::BTreeSet::new();
    let mut paths = std::collections::BTreeSet::new();
    let mut inputs = Vec::new();
    for line in manifest.lines() {
        let (name,path) = line.split_once('\t').ok_or("batch list needs name TAB path")?;
        if name.is_empty() || name.contains(['/', '\\']) ||
                !names.insert(name.to_owned()) || !paths.insert(path.to_owned()) {
            return Err("duplicate/invalid batch input identity".into());
        }
        inputs.push((name.to_owned(),std::fs::read(path)?));
    }
    if inputs.is_empty() { return Err("empty resident input unit".into()); }
    // Owned result memory is allocated and faulted in before the ready stop, so
    // the timed region measures parsing rather than first-touch page faults.
    let mut results: Vec<Vec<u8>> = inputs.iter().map(|(_, bytes)| {
        let mut buffer = Vec::with_capacity(2 * bytes.len() + 65536);
        buffer.resize(buffer.capacity(), 0xA5);
        buffer.clear();
        buffer
    }).collect();
    std::fs::create_dir_all(output)?;
    if resident { stop(); }
    for ((_,bytes),encoded) in inputs.iter().zip(results.iter_mut()) {
        let mut de = serde_json::Deserializer::from_slice(bytes);
        let mut sink = flat::Sink(encoded);
        serde::de::DeserializeSeed::deserialize(flat::CollectionSeed(&mut sink), &mut de)?;
        de.end()?;
    }
    if resident { stop(); }
    for ((name,_),bytes) in inputs.into_iter().zip(results) {
        std::fs::write(output.join(format!("{name}.bin")),bytes)?;
    }
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args_os().collect();
    if args.len() == 4 && (args[1] == "--resident-batch" || args[1] == "--check-batch") {
        return batch(Path::new(&args[2]),Path::new(&args[3]),args[1] == "--resident-batch");
    }
    Err("usage: --resident-batch|--check-batch LIST OUTPUT_DIR".into())
}
