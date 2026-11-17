#!/usr/bin/env python3
"""Verifier-only identity ledger; subprocess success without identities is insufficient."""
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path("/scratch")
release = json.loads(Path(__file__).with_name("upstream-config.json").read_text())
source = ROOT / "source"
command = list(release["upstream_test_command"])
environment = dict(os.environ)
environment.update(CARGO_TARGET_DIR="/scratch/upstream-target", CARGO_BUILD_JOBS="8",
                   GOCACHE="/scratch/go-cache", GOPROXY="off", GOSUMDB="off",
                   GOPATH="/go", GOMODCACHE="/go/pkg/mod",
                   PATH="/usr/local/cargo/bin:/usr/local/go/bin:" + os.environ["PATH"],
                   RUSTUP_HOME="/usr/local/rustup",
                   RUSTUP_TOOLCHAIN="1.97.1" if release["task"] in ("lumen-js", "spacewasm-interp") else "1.99.0")
if command[0] == "cargo":
    import shutil
    shutil.copytree("/usr/local/cargo/registry", ROOT / "cargo-home/registry")
    environment["CARGO_HOME"] = "/scratch/cargo-home"
if command[0] == "go":
    command.insert(2, "-json")
else:
    command = ["/bin/sh", "-c", 'exec "$@" 2>&1', "cargo-ledger", *command]
with (ROOT / "upstream.stdout").open("wb") as out, (ROOT / "upstream.stderr").open("wb") as err:
    result = subprocess.run(command, cwd=source, env=environment, stdout=out, stderr=err)
log = (ROOT / "upstream.stdout").read_text(errors="replace")
outcomes = {}
if release["upstream_test_command"][0] == "go":
    for line in log.splitlines():
        event = json.loads(line)
        if event.get("Test") and event["Action"] in ("pass", "skip", "fail"):
            identity = event["Package"] + "::" + event["Test"]
            if identity in outcomes:
                raise ValueError("Duplicate upstream identity: " + identity)
            outcomes[identity] = {"pass": "passed", "skip": "skipped", "fail": "failed"}[event["Action"]]
else:
    target = None
    for line in log.splitlines():
        if re.match(r"\s*(Running |Doc-tests )", line):
            target = re.sub(r"\([^)]*/([^/]+)-[a-f0-9]+\)", r"(\1)", line.strip())
        match = re.match(r"^test (.+) \.\.\. (ok|ignored|FAILED)(?:[, ].*)?$", line)
        if match:
            if target is None:
                raise ValueError("Test identity has no target")
            identity = target + "::" + match[1]
            if identity in outcomes:
                raise ValueError("Duplicate upstream identity")
            outcomes[identity] = {"ok": "passed", "ignored": "skipped", "FAILED": "failed"}[match[2]]
report = {"passed": result.returncode == 0 and bool(outcomes) and
          all(status in ("passed", "skipped") for status in outcomes.values()),
          "command": release["upstream_test_command"], "outcomes": outcomes,
          "exit_code": result.returncode}
(ROOT / "upstream.json").write_text(json.dumps(report, indent=2) + "\n")
raise SystemExit(0 if report["passed"] else 1)
