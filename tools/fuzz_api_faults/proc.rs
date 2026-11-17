//! Per-public-call Linux snapshots for native verifier drivers.
//! Call capture() before and after EACH public API call, not only at exit.
//! Err means unavailable; never convert it into an empty/zero snapshot.
use std::collections::BTreeSet;
use std::fs;
use std::io;
use std::path::PathBuf;
use std::os::unix::ffi::OsStrExt;
use std::sync::OnceLock;
use std::time::Instant;
#[path = "path_hash.rs"]
mod path_hash;

#[derive(Debug, Clone)]
pub struct FdIdentity { pub fd: u32, pub target: String, pub target_sha256: String }
#[derive(Debug, Clone)]
pub struct ProcessIdentity { pub pid: u32, pub start_ticks: u64, pub state: String }
#[derive(Debug, Clone)]
pub struct ThreadIdentity {
    pub tid: u32, pub start_ticks: u64, pub state: String,
    pub tgid: Option<u32>, pub scope_pid: u32, pub flags: u64,
}
#[derive(Debug, Clone)]
pub struct ResourceSnapshot {
    pub pid: u32,
    pub proc_pid: u32,
    pub fds: Vec<FdIdentity>,
    /// Compatibility only: derived from the same live identities. Do not
    /// interpret an empty vector as zero when threads_count is None.
    pub threads: Vec<u32>,
    pub threads_count: Option<usize>,
    pub thread_identities: Option<Vec<ThreadIdentity>>,
    /// Diagnostic witnesses only, not live worker identities. Linux do_exit
    /// can leave PF_EXITING tasks visible after pthread_join returns.
    pub excluded_exiting_threads: Vec<ThreadIdentity>,
    pub thread_error: Option<String>,
    pub thread_method: String,
    pub phase: String,
    pub sample_started_ns: u128,
    pub sample_finished_ns: u128,
    pub clock_method: String,
    pub raw_method: String,
    pub children: Option<Vec<ProcessIdentity>>,
    pub children_method: String,
    pub children_error: Option<String>,
}

fn dead(state: &str) -> bool { matches!(state, "Z" | "X" | "x") }
pub const PF_EXITING: u64 = 0x00000004;
fn clock_ns() -> u128 {
    static ORIGIN: OnceLock<Instant> = OnceLock::new();
    ORIGIN.get_or_init(Instant::now).elapsed().as_nanos()
}
fn stat_parts(text: &str) -> io::Result<(u32, u32, u64, String, u64)> {
    let pid = text.split_whitespace().next().and_then(|s| s.parse().ok())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "missing proc pid"))?;
    let fields: Vec<_> = text.rsplit_once(')').ok_or_else(|| io::Error::new(
        io::ErrorKind::InvalidData, "malformed proc stat"))?.1.split_whitespace().collect();
    let ppid = fields.get(1).and_then(|s| s.parse().ok()).ok_or_else(||
        io::Error::new(io::ErrorKind::InvalidData, "missing parent pid"))?;
    let ticks = fields.get(19).and_then(|s| s.parse().ok()).ok_or_else(||
        io::Error::new(io::ErrorKind::InvalidData, "missing start ticks"))?;
    let state = fields.first().ok_or_else(|| io::Error::new(
        io::ErrorKind::InvalidData, "missing task state"))?.to_string();
    let flags = fields.get(6).and_then(|s| s.parse().ok()).ok_or_else(||
        io::Error::new(io::ErrorKind::InvalidData, "missing kernel task flags"))?;
    Ok((pid, ppid, ticks, state, flags))
}
fn live_threads(base: &PathBuf, scope_pid: u32,
                excluded: &mut Vec<ThreadIdentity>) -> io::Result<Vec<ThreadIdentity>> {
    let mut rows = Vec::new();
    for tid in ids(base.join("task"))? {
        let task = base.join(format!("task/{tid}"));
        let data = match fs::read_to_string(task.join("stat")) {
            Ok(value) => value,
            Err(e) if e.kind() == io::ErrorKind::NotFound && !task.exists() => continue,
            Err(e) => return Err(e),
        };
        let (first_tid, _, first_ticks, state, flags) = stat_parts(&data)?;
        if flags & PF_EXITING != 0 {
            excluded.push(ThreadIdentity { tid: first_tid, start_ticks: first_ticks,
                state, tgid: None, scope_pid, flags });
            continue;
        }
        if dead(&state) { continue; }
        let tgid = fs::read_to_string(task.join("status")).ok().and_then(|value|
            value.lines().find(|line| line.starts_with("Tgid:"))
                .and_then(|line| line.split_whitespace().nth(1)).and_then(|n| n.parse().ok()));
        let data = match fs::read_to_string(task.join("stat")) {
            Ok(value) => value,
            Err(e) if e.kind() == io::ErrorKind::NotFound && !task.exists() => continue,
            Err(e) => return Err(e),
        };
        let (current_tid, _, ticks, state, flags) = stat_parts(&data)?;
        if (first_tid, first_ticks) != (current_tid, ticks) {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "thread identity raced"));
        }
        let row = ThreadIdentity { tid: current_tid, start_ticks: ticks, state, tgid, scope_pid, flags };
        if flags & PF_EXITING != 0 {
            excluded.push(row);
        } else if !dead(&row.state) {
            rows.push(row);
        }
    }
    if rows.is_empty() {
        return Err(io::Error::new(io::ErrorKind::Unsupported, "live thread identities unavailable"));
    }
    rows.sort_by_key(|row| row.tid);
    if rows.windows(2).any(|pair| pair[0].tid == pair[1].tid) {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "duplicate live tid"));
    }
    Ok(rows)
}

impl ResourceSnapshot {
    /// Missing identities are pending; mismatched counts are invalid evidence.
    pub fn validate_thread_counts(&self) -> io::Result<bool> {
        let rows = match &self.thread_identities { Some(rows) => rows, None => return Ok(false) };
        let tids: Vec<u32> = rows.iter().map(|r| r.tid).collect();
        let unique: BTreeSet<u32> = tids.iter().copied().collect();
        if self.threads_count != Some(rows.len()) || self.threads != tids || unique.len() != rows.len()
            || rows.iter().any(|r| r.tid == 0 || r.scope_pid == 0 || dead(&r.state)
                || r.flags & PF_EXITING != 0)
            || self.sample_finished_ns < self.sample_started_ns || self.phase.is_empty() {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "thread identity/count/clock mismatch"));
        }
        Ok(true)
    }
}

fn ids(path: PathBuf) -> io::Result<Vec<u32>> {
    let mut values = Vec::new();
    for entry in fs::read_dir(path)? {
        if let Some(n) = entry?.file_name().to_str().and_then(|s| s.parse().ok()) { values.push(n); }
    }
    values.sort_unstable();
    Ok(values)
}

fn child_pids(pid: u32) -> io::Result<Vec<u32>> {
    let base = PathBuf::from(format!("/proc/{pid}/task"));
    let mut children = Vec::new();
    let tids = ids(base.clone())?;
    if tids.is_empty() {
        return Err(io::Error::new(io::ErrorKind::Unsupported, "no visible task identities"));
    }
    for tid in tids {
        match fs::read_to_string(base.join(format!("{tid}/children"))) {
            Ok(data) => children.extend(data.split_whitespace().filter_map(|s| s.parse::<u32>().ok())),
            Err(e) if e.kind() == io::ErrorKind::NotFound => {
                if base.join(tid.to_string()).exists() {
                    return Err(io::Error::new(io::ErrorKind::Unsupported, "task children unavailable"));
                }
            },
            Err(e) => return Err(e),
        }
    }
    Ok(children)
}

fn descendants(pid: u32) -> io::Result<Vec<ProcessIdentity>> {
    let mut todo = child_pids(pid)?;
    let mut seen = BTreeSet::new();
    let mut children = Vec::new();
    while let Some(child) = todo.pop() {
        if !seen.insert(child) { continue; }
        if seen.len() > 4096 {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "descendant snapshot bound"));
        }
        match fs::read_to_string(format!("/proc/{child}/stat")) {
            Ok(stat) => {
                let after = stat.rsplit_once(')').ok_or_else(|| io::Error::new(
                    io::ErrorKind::InvalidData, "malformed process identity"))?.1;
                let fields: Vec<_> = after.split_whitespace().collect();
                let start_ticks = fields.get(19).and_then(|s| s.parse().ok()).ok_or_else(||
                    io::Error::new(io::ErrorKind::InvalidData, "missing process start ticks"))?;
                let state = fields[0].to_owned();
                if !dead(&state) { children.push(ProcessIdentity { pid: child, start_ticks, state }); }
                match child_pids(child) {
                    Ok(more) => todo.extend(more),
                    Err(e) if e.kind() == io::ErrorKind::NotFound => {},
                    Err(e) => return Err(e),
                }
            },
            Err(e) if e.kind() == io::ErrorKind::NotFound => {},
            Err(e) => return Err(e),
        }
    }
    children.sort_by_key(|c| c.pid);
    Ok(children)
}

fn ppid_scan(parent: u32) -> io::Result<Vec<ProcessIdentity>> {
    let mut rows = Vec::new();
    for directory_pid in ids(PathBuf::from("/proc"))? {
        match fs::read_to_string(format!("/proc/{directory_pid}/stat")) {
            Ok(stat) => {
                let pid: u32 = stat.split_whitespace().next().and_then(|s| s.parse().ok())
                    .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "missing proc pid"))?;
                let after = stat.rsplit_once(')').ok_or_else(|| io::Error::new(
                    io::ErrorKind::InvalidData, "malformed process identity"))?.1;
                let fields: Vec<_> = after.split_whitespace().collect();
                let ppid: u32 = fields.get(1).and_then(|s| s.parse().ok()).ok_or_else(||
                    io::Error::new(io::ErrorKind::InvalidData, "missing parent pid"))?;
                let start_ticks: u64 = fields.get(19).and_then(|s| s.parse().ok()).ok_or_else(||
                    io::Error::new(io::ErrorKind::InvalidData, "missing start ticks"))?;
                rows.push((pid, ppid, start_ticks, fields[0].to_owned()));
            },
            Err(e) if e.kind() == io::ErrorKind::NotFound => {},
            Err(e) => return Err(e),
        }
    }
    if !rows.iter().any(|(pid, _, _, _)| *pid == parent) {
        return Err(io::Error::new(io::ErrorKind::Unsupported, "parent outside proc-stat namespace"));
    }
    let mut todo = vec![parent];
    let mut seen = BTreeSet::from([parent]);
    let mut children = Vec::new();
    while let Some(current) = todo.pop() {
        for (pid, ppid, start_ticks, state) in &rows {
            if *ppid == current && seen.insert(*pid) {
                if seen.len() > 4096 {
                    return Err(io::Error::new(io::ErrorKind::InvalidData, "descendant bound"));
                }
                todo.push(*pid);
                if !dead(state) {
                    children.push(ProcessIdentity { pid: *pid, start_ticks: *start_ticks, state: state.clone() });
                }
            }
        }
    }
    children.sort_by_key(|c| c.pid);
    Ok(children)
}

pub fn capture() -> io::Result<ResourceSnapshot> {
    capture_phase("sample")
}

pub fn capture_phase(phase: &str) -> io::Result<ResourceSnapshot> {
    let sample_started_ns = clock_ns();
    let pid = std::process::id();
    let base = PathBuf::from("/proc/self");
    let stat = fs::read_to_string(base.join("stat"))?;
    let proc_pid: u32 = stat.split_whitespace().next().and_then(|s| s.parse().ok())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "missing self proc pid"))?;
    let mut excluded_exiting_threads = Vec::new();
    let (thread_identities, thread_error) = match live_threads(&base, proc_pid, &mut excluded_exiting_threads) {
        Ok(rows) => (Some(rows), None),
        Err(e) => (None, Some(e.to_string())),
    };
    let threads_count = thread_identities.as_ref().map(Vec::len);
    let threads = thread_identities.as_ref().map(|rows| rows.iter().map(|r| r.tid).collect()).unwrap_or_default();
    let mut fds = Vec::new();
    for fd in ids(base.join("fd"))? {
        match fs::read_link(base.join(format!("fd/{fd}"))) {
            Ok(path) => {
                let digest = path_hash::sha256(path.as_os_str().as_bytes());
                fds.push(FdIdentity { fd, target: format!("sha256:{digest}"), target_sha256: digest });
            },
            Err(e) if e.kind() == io::ErrorKind::NotFound => {},
            Err(e) => return Err(e),
        }
    }
    let (children, method) = match descendants(proc_pid) {
        Ok(children) => (Ok(children), "task-children"),
        Err(e) if e.kind() == io::ErrorKind::Unsupported || e.kind() == io::ErrorKind::NotFound
            => (ppid_scan(proc_pid), "proc-stat-ppid-fallback"),
        Err(e) => (Err(e), "unavailable"),
    };
    let (children, children_error) = match children {
        Ok(rows) => (Some(rows), None),
        Err(e) => (None, Some(e.to_string())),
    };
    Ok(ResourceSnapshot { pid, proc_pid, fds, threads, threads_count, thread_identities,
        excluded_exiting_threads, thread_error,
        thread_method: "proc-task-stat-starttime-live-state-flags-pf-exiting".to_owned(),
        phase: phase.to_owned(), sample_started_ns, sample_finished_ns: clock_ns(),
        clock_method: "std::time::Instant process-local; scope=pid".to_owned(),
        raw_method: "proc-self/task-stat-status+fd-link-sha256+descendants".to_owned(), children,
        children_method: method.to_owned(), children_error })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture_stat(tid: u32, state: &str, flags: u64) -> String {
        let mut fields = vec!["0".to_owned(); 22];
        fields[0] = state.to_owned();
        fields[1] = "100".to_owned();
        fields[6] = flags.to_string();
        fields[19] = (tid + 10).to_string();
        format!("{tid} (name with ) parentheses) {}\n", fields.join(" "))
    }
    #[test]
    fn proc_flags_are_field_nine() {
        let (tid, _, ticks, state, flags) = stat_parts(&fixture_stat(42, "R", 4194380)).unwrap();
        assert_eq!((tid, ticks, state.as_str(), flags), (42, 52, "R", 0x40004c));
        assert_ne!(flags & PF_EXITING, 0);
    }
    #[test]
    fn exiting_running_task_is_diagnostic_not_live() {
        let base = std::env::temp_dir().join(format!("proc-flags-{}-{}", std::process::id(), clock_ns()));
        for (tid, state, flags) in [(1, "R", 4194368), (2, "R", 4194380),
                                   (3, "S", 4194368), (4, "D", 4194368), (5, "Z", 0)] {
            let task = base.join(format!("task/{tid}"));
            fs::create_dir_all(&task).unwrap();
            fs::write(task.join("stat"), fixture_stat(tid, state, flags)).unwrap();
            fs::write(task.join("status"), "Tgid:\t100\n").unwrap();
        }
        let mut excluded = Vec::new();
        let rows = live_threads(&base, 100, &mut excluded).unwrap();
        fs::remove_dir_all(base).unwrap();
        assert_eq!(rows.iter().map(|r| r.tid).collect::<Vec<_>>(), vec![1, 3, 4]);
        assert_eq!(excluded.len(), 1);
        assert_eq!((excluded[0].tid, excluded[0].start_ticks, excluded[0].flags),
                   (2, 12, 4194380));
        assert_eq!(excluded[0].state, "R");
    }
    #[test]
    fn joined_and_live_detached_workers_have_no_name_allowance() {
        use std::sync::mpsc::channel;
        let (identity_tx, identity_rx) = channel();
        let (release_tx, release_rx) = channel();
        let worker = std::thread::spawn(move || {
            let (tid, _, ticks, _, _) = stat_parts(
                &fs::read_to_string("/proc/thread-self/stat").unwrap()).unwrap();
            identity_tx.send((tid, ticks)).unwrap();
            release_rx.recv().unwrap();
        });
        let identity = identity_rx.recv().unwrap();
        let before = capture().unwrap();
        assert!(before.thread_identities.unwrap().iter().any(|r|
            (r.tid, r.start_ticks) == identity && r.flags & PF_EXITING == 0));
        release_tx.send(()).unwrap();
        worker.join().unwrap();
        let after = capture().unwrap();
        assert!(!after.thread_identities.unwrap().iter().any(|r| (r.tid, r.start_ticks) == identity));

        let (identity_tx, identity_rx) = channel();
        let (release_tx, release_rx) = channel();
        let (done_tx, done_rx) = channel();
        let detached = std::thread::spawn(move || {
            let (tid, _, ticks, _, _) = stat_parts(
                &fs::read_to_string("/proc/thread-self/stat").unwrap()).unwrap();
            identity_tx.send((tid, ticks)).unwrap();
            release_rx.recv().unwrap();
            done_tx.send(()).unwrap();
        });
        drop(detached); // Dropping the join handle must not exempt a live task.
        let identity = identity_rx.recv().unwrap();
        let snapshot = capture().unwrap();
        let present = snapshot.thread_identities.unwrap().iter().any(|r|
            (r.tid, r.start_ticks) == identity && r.flags & PF_EXITING == 0);
        release_tx.send(()).unwrap();
        done_rx.recv().unwrap();
        assert!(present);
    }
    #[test]
    fn records_current_worker_and_descriptors() {
        let snap = capture_phase("before").unwrap();
        assert_eq!(snap.pid, std::process::id());
        assert!(snap.fds.len() >= 3);
        assert!(!snap.threads.is_empty());
        assert!(snap.validate_thread_counts().unwrap());
        assert_eq!(snap.phase, "before");
        assert_eq!(snap.threads_count, snap.thread_identities.as_ref().map(Vec::len));
        assert!(snap.fds.iter().all(|fd| fd.target.starts_with("sha256:") && fd.target_sha256.len() == 64));
    }
    #[test]
    fn joined_worker_is_not_reported_as_surviving() {
        let thread = std::thread::spawn(|| {});
        thread.join().unwrap();
        // Rust test harness itself has other threads. The joined child TID
        // is gone; do not impose an invented global one-thread policy.
        assert!(!capture().unwrap().threads.is_empty());
    }
    #[test]
    fn ppid_fallback_records_a_live_child_without_cmdline_or_env() {
        let mut child = std::process::Command::new("sleep").arg("10").spawn().unwrap();
        let rows = ppid_scan(capture().unwrap().proc_pid);
        child.kill().unwrap();
        child.wait().unwrap();
        let rows = rows.unwrap();
        assert!(rows.iter().any(|r| r.start_ticks > 0));
    }
    #[test]
    fn missing_identity_is_pending_but_count_mismatch_is_invalid() {
        let mut snap = capture().unwrap();
        snap.threads_count = Some(snap.threads_count.unwrap() + 1);
        assert!(snap.validate_thread_counts().is_err());
        snap.thread_identities = None;
        snap.threads_count = None;
        assert!(!snap.validate_thread_counts().unwrap());
    }
    #[test]
    fn dead_states_are_filtered_without_name_rules() {
        for state in ["Z", "X", "x"] { assert!(dead(state)); }
        for state in ["R", "S", "D", "I", "T"] { assert!(!dead(state)); }
    }
}
