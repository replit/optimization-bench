"""Real exported bin/terser I/O observations; no stderr normalization or verdict."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess

def blob(data):
    return {"encoding": "base64", "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "data": base64.b64encode(data).decode("ascii")}

def files(root):
    result = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_file():
            result[relative] = {"kind": "file", "mode": path.stat().st_mode & 0o777,
                                "content": blob(path.read_bytes())}
        elif path.is_dir():
            result[relative] = {"kind": "directory", "mode": path.stat().st_mode & 0o777}
    return result

def run(project):
    assert os.geteuid() != 0, "Readonly-output fault must run unprivileged"
    project = Path(project).resolve()
    binary = project / "bin/terser"
    assert binary.is_file()
    work = Path("/tmp/terser-cli-probe")
    work.mkdir()
    source = b"console.log(1);\n"
    records = []
    definitions = [
        ("stdin-success", "capture", [], source),
        ("file-output-success", "capture", ["input.js", "--output", "output.js"], None),
        ("dev-full", "dev-full", [], source),
        ("closed-reader-EPIPE", "epipe", [], source),
        ("closed-stdout", "closed", [], source),
        ("readonly-output", "capture", ["input.js", "--output", "readonly/out.js"], None),
        ("missing-input", "capture", ["missing.js"], None),
        ("syntax-error", "capture", [], b"if (\n"),
    ]
    for label, mode, arguments, data in definitions:
        case_root = work / label
        case_root.mkdir()
        (case_root / "input.js").write_bytes(source)
        (case_root / "readonly").mkdir(mode=0o555)
        before = files(case_root)
        # Optional -m/-c option arguments would consume a trailing filename.
        # Place positional files/output arguments first, as documented upstream.
        command = ["node", str(binary), *arguments, "--compress", "--mangle"]
        stdout, handle, write_fd = subprocess.PIPE, None, None
        preexec = None
        if mode == "dev-full":
            handle = open("/dev/full", "wb", buffering=0)
            stdout = handle
        elif mode == "epipe":
            read_fd, write_fd = os.pipe()
            os.close(read_fd) # Closed BEFORE child writes: not scheduler-dependent.
            stdout = write_fd
        elif mode == "closed":
            stdout = subprocess.DEVNULL
            def close_stdout():
                os.close(1)
            preexec = close_stdout
        environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp",
                       "LC_ALL": "C.UTF-8", "TZ": "UTC"}
        try:
            process = subprocess.run(command, input=data, stdin=None if data is not None else subprocess.DEVNULL,
                                     stdout=stdout, stderr=subprocess.PIPE, cwd=case_root,
                                     env=environment, preexec_fn=preexec, timeout=20)
        finally:
            if handle is not None:
                handle.close()
            if write_fd is not None:
                os.close(write_fd)
        stderr = process.stderr
        record = {
            "id": label, "argv": command, "stdio_fault": mode, "uid": os.geteuid(),
            "termination": {"kind": "signal" if process.returncode < 0 else "return",
                            "exit_code": process.returncode},
            "stdout": blob(process.stdout) if process.stdout is not None else {
                "available": False, "reason": "Fault sink/closed descriptor not a readable capture"},
            "stderr": blob(stderr), "stderr_normalization": "none",
            "observed_errno_tokens": [code for code in ["ENOSPC", "EPIPE", "EBADF", "EACCES", "ENOENT"]
                                      if code.encode() in stderr],
            "files_before": before, "files_after": files(case_root),
            "candidate_verdict": None,
        }
        if label == "stdin-success":
            assert process.returncode == 0 and process.stdout and b"console.log(1)" in process.stdout
        if label == "file-output-success":
            assert process.returncode == 0 and (case_root / "output.js").read_bytes()
        records.append(record)
    return {
        "format": "optimbench-terser-cli-io-facts-v1", "task": "terser-minify",
        "public_entry": "package.json exports ./bin/terser",
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "records": records, "timing_performed": False, "candidate_verdict": None,
        "policy": "Complete raw stdout/stderr/files; shared controller attributes semantic failures",
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.project)
    args.out.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"records": len(result["records"]), "uid": os.geteuid(),
                      "receipt_sha256": hashlib.sha256(args.out.read_bytes()).hexdigest()}))
