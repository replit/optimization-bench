"""Verifier-only launcher. No timing and no network; return complete byte outputs."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

case = json.loads(Path(sys.argv[1]).read_text())
scratch = Path("/scratch")
if case["kind"] == "api":
    module = scratch / "api"
    module.mkdir()
    (module / "go.mod").write_text(
        "module differential\n\ngo 1.26\n\nrequire github.com/yuin/goldmark/v2 v2.0.0\n"
        "replace github.com/yuin/goldmark/v2 => /app/build/project\n")
    environment = {
        **os.environ, "PATH": "/usr/local/go/bin:" + os.environ.get("PATH", ""),
        "GOCACHE": "/scratch/go-cache", "GOMODCACHE": "/scratch/go-mod-cache",
        "GOPROXY": "file:///app/offline-module-proxy", "GOSUMDB": "off",
        "GOTOOLCHAIN": "local", "CGO_ENABLED": "0", "GOMAXPROCS": "8",
        "GOFLAGS": "-buildvcs=false",
    }
    build = subprocess.run(
        ["go", "build", "-mod=mod", "-o", "/scratch/api-run",
         "/verifier-differential/main.go"],
        cwd=module, env=environment, capture_output=True)
    if build.returncode:
        sys.stderr.buffer.write(build.stderr)
        raise SystemExit(build.returncode)
    run = subprocess.run(["/scratch/api-run", sys.argv[1]],
                         capture_output=True, env=environment)
    sys.stdout.buffer.write(run.stdout)
    sys.stderr.buffer.write(run.stderr)
    raise SystemExit(run.returncode)

source = case["source"].encode()
if case.get("real_fixture_index") is not None:
    # Source bytes come from the frozen public manifest, never held-out inputs.
    manifest = json.loads(Path("/app/workload/inputs/manifest.json").read_text())
    def members(value):
        if isinstance(value, dict):
            if "sha256" in value and "original_path" in value:
                yield value
            else:
                for item in value.values():
                    yield from members(item)
        elif isinstance(value, list):
            for item in value:
                yield from members(item)
    rows = list(members(manifest))
    row = rows[case["real_fixture_index"] % len(rows)]
    # Payload sources use the original path or content-addressed files layout.
    paths = [Path("/app/workload/inputs") / row["original_path"],
             Path("/app/workload/inputs/files") / row["sha256"],
             Path("/app/workload/inputs") / row["sha256"]]
    path = next((p for p in paths if p.is_file()), None)
    if path is None:
        raise RuntimeError("Frozen public input missing; no fallback fixture")
    data = path.read_bytes()
    start = min(len(data), case["real_slice"] * 1024)
    source = data[start:start + 4096] + b"\n\n" + source
if case["newline"] == "crlf":
    source = source.replace(b"\n", b"\r\n")
elif case["newline"] == "cr":
    source = source.replace(b"\n", b"\r")
source += bytes.fromhex(case["invalid_suffix_hex"])
other = b"# independent\n\n- [ ] separate\n\n& < >\n"
# A,B,A inside one batch, then the same batch in independent processes:
# catches cross-document and cross-invocation reuse without inventing an API.
rows = []
for index, raw in enumerate((source, other, source)):
    (scratch / f"input-{index}").write_bytes(raw)
    rows.append(f"result-{index}\tinput-{index}")
(scratch / "rows.tsv").write_text("\n".join(rows) + "\n")
results = []
for _ in range(case["repeat"]):
    for path in scratch.glob("result-*.*"):
        path.unlink()
    result = subprocess.run(["/app/build/goldmark-gfm-html", "rows.tsv"],
                            cwd=scratch, capture_output=True)
    outputs = []
    for index in range(3):
        outputs.append({suffix: base64.b64encode(path.read_bytes()).decode()
                        for suffix in ("out", "err")
                        if (path := scratch / f"result-{index}.{suffix}").exists()})
    results.append({"exit_code": result.returncode,
                    "stdout_b64": base64.b64encode(result.stdout).decode(),
                    "stderr_b64": base64.b64encode(result.stderr).decode(),
                    "outputs": outputs})
print(json.dumps(results, sort_keys=True))
