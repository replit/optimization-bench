// Protected v1.1 resident ingestion driver. Library source/deps are unchanged.
// Both arms pre-read every DISTINCT complete file before the ready stop.
// Between stops: typed ingestion, full serialization into owned memory, and
// per-file parsed-value drop. Disk reads and full result export are untimed.
use std::io::{self, Write};
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
    std::fs::create_dir_all(output)?;
    if resident { stop(); }
    let mut results = Vec::with_capacity(inputs.len());
    for (_,bytes) in &inputs {
        let metadata = crates_index::Crate::from_slice(bytes)?;
        let mut encoded = Vec::new();
        serde_json::to_writer(&mut encoded,&metadata)?;
        drop(metadata);
        results.push(encoded);
    }
    if resident { stop(); }
    for ((name,_),bytes) in inputs.into_iter().zip(results) {
        std::fs::write(output.join(format!("{name}.json")),bytes)?;
    }
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args_os().collect();
    if args.get(1).is_some_and(|arg| arg == "--resident-batch" || arg == "--check-batch") {
        if args.len() != 4 { return Err("batch mode requires list and output directory".into()); }
        return batch(Path::new(&args[2]),Path::new(&args[3]),args[1] == "--resident-batch");
    }
    // Keep the original one-file protected correctness entrypoint. Official
    // v1.1 units use resident-batch; this compatibility path is never repeated
    // to pad a timed unit and is not a public serde_json CLI.
    let path = args.get(1).ok_or("one index file required")?;
    let bytes = std::fs::read(path)?;
    let metadata = crates_index::Crate::from_slice(&bytes)?;
    let mut out = io::BufWriter::new(io::stdout().lock());
    serde_json::to_writer(&mut out, &metadata)?;
    out.write_all(b"\n")?;
    out.flush()?;
    Ok(())
}
