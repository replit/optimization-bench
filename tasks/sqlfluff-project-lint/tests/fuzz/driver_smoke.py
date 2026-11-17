#!/usr/bin/env python3
"""Baseline-only driver transport/replay smoke; NOT task fuzz qualification."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile

import adapter

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--pristine-build", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError("fresh receipt path required")
    args.out.mkdir(parents=True)
    manifest = []
    for path in sorted(args.pristine_build.rglob("*")):
        if path.is_file() and not path.is_symlink():
            manifest.append([path.relative_to(args.pristine_build).as_posix(),
                             path.stat().st_mode & 0o777, hashlib.sha256(path.read_bytes()).hexdigest()])
    build_sha = hashlib.sha256(json.dumps(manifest, separators=(",", ":")).encode()).hexdigest()
    image_id = subprocess.check_output(["docker", "image", "inspect", args.image,
                                        "--format", "{{.Id}}"], text=True).strip()
    cases = list(adapter.generate(random.Random(1729), {"cases": len(adapter.OPERATIONS)}))
    rows = []
    for case in cases:
        observations = []
        executions = []
        for repetition in range(2):
            with tempfile.TemporaryDirectory(prefix="sqlfluff-driver-smoke-") as directory:
                scratch = Path(directory)
                scratch.chmod(0o777)
                (scratch / "case.json").write_text(json.dumps(case))
                argv = ["docker", "run", "--rm", "--network", "none", "--read-only",
                        "--user", "10000:10000", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                        "--cpuset-cpus", "0", "--memory", "16g", "--pids-limit", "512",
                        "--tmpfs", "/tmp:rw,nosuid,nodev,size=1g",
                        "-v", f"{HERE}:/task-fuzz:ro", "-v", f"{scratch}:/scratch:rw",
                        "--entrypoint", "sh", image_id, "-c",
                        "python /task-fuzz/driver.py --runtime /app/baseline-build/runtime "
                        "--case /scratch/case.json --build-sha256 " + build_sha +
                        " --report-fd 3 3>/scratch/report.json"]
                run = subprocess.run(argv, capture_output=True, timeout=90)
                observation = json.loads((scratch / "report.json").read_text()) \
                    if (scratch / "report.json").is_file() else None
                executions.append({"repetition": repetition, "driver_exit": run.returncode,
                                   "stdout_base64": __import__("base64").b64encode(run.stdout).decode(),
                                   "stderr_base64": __import__("base64").b64encode(run.stderr).decode(),
                                   "argv": argv})
                observations.append(observation)
        transport_valid = all(x is not None for x in observations) and all(e["driver_exit"] == 0 for e in executions)
        compared = adapter.compare(*observations, case) if transport_valid else None
        usage_error = any(o and o.get("effects", {}).get("exit_code") == 2 for o in observations)
        rows.append({"case": case, "observations": observations, "executions": executions,
                     "transport_valid": transport_valid, "cli_usage_error": usage_error,
                     "comparison": compared})
        print(case["operation"], "transport", transport_valid,
              "CLI_usage_error", usage_error, "equal", compared.get("equal") if compared else None, flush=True)
    value = {
        "format": "optimbench-sqlfluff-driver-smoke-v1",
        "purpose": "baseline-only arm transport and fresh-worker repeat smoke; not fuzz/coverage qualification",
        "image": image_id, "build_sha256": build_sha,
        "build_digest_algorithm": "SHA256 of JSON sorted [path, mode, fileSHA256] manifest",
        "adapter_sha256": hashlib.sha256((HERE / "adapter.py").read_bytes()).hexdigest(),
        "driver_sha256": hashlib.sha256((HERE / "driver.py").read_bytes()).hexdigest(),
        "cases": len(cases), "workers": len(cases) * 2,
        "transport_valid": all(r["transport_valid"] for r in rows),
        "CLI_usage_errors": sum(r["cli_usage_error"] for r in rows),
        "all_equal": all(r["comparison"] and r["comparison"]["equal"] for r in rows),
        "coverage_claim": False, "cpu_1_2_8_matrix_claim": False,
        "rows": rows,
    }
    with gzip.open(args.out / "observations.json.gz", "wb") as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2).encode())
    summary = {k: v for k, v in value.items() if k != "rows"}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if value["transport_valid"] and not value["CLI_usage_errors"] and value["all_equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
