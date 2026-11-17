#!/usr/bin/env python3
"""Verifier-only default and supported feature identity/outcome ledger."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

EXTERNAL_COMMAND_CONTEXTS = {"<module>": "pinned-worker"}

ROOT = Path("/scratch")
env = {**os.environ, "CARGO_TARGET_DIR":"/scratch/upstream-target",
       "CARGO_HOME":"/scratch/cargo-home","CARGO_NET_OFFLINE":"true",
       "CARGO_BUILD_JOBS":"8", "RUSTUP_HOME":"/usr/local/rustup",
       "RUSTUP_TOOLCHAIN":"1.93.1",
       "PATH":"/usr/local/cargo/bin:"+os.environ["PATH"]}
shutil.copytree("/usr/local/cargo/registry", ROOT/"cargo-home/registry")
outcomes, commands, exits = {}, [], []
variants = [
    ("default", ["cargo","test","--all-targets","--offline","--locked"]),
    *[(features, ["cargo","test","--offline","--locked","--features",features,
                  "--tests","--","--skip","ui","--exact"])
      for features in ("preserve_order","float_roundtrip","arbitrary_precision",
                       "float_roundtrip,arbitrary_precision","raw_value","unbounded_depth")],
    ("alloc",["cargo","check","--offline","--locked","--no-default-features","--features","alloc"]),
    ("alloc-arbitrary",["cargo","check","--offline","--locked","--no-default-features",
                        "--features","alloc,arbitrary_precision"]),
    ("alloc-raw",["cargo","check","--offline","--locked","--no-default-features",
                  "--features","alloc,raw_value"])
]
for label, command in variants:
    commands.append({"variant":label,"command":command})
    run = subprocess.run(command, cwd=ROOT/"source", env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    exits.append(run.returncode)
    log = run.stdout.decode(errors="replace")
    (ROOT/f"upstream-{label.replace(',','-')}.log").write_text(log)
    target = None
    for line in log.splitlines():
        if re.match(r"\s*(Running |Doc-tests )",line):
            target = re.sub(r"\([^)]*/([^/]+)-[a-f0-9]+\)",r"(\1)",line.strip())
        match = re.match(r"^test (.+) \.\.\. (ok|ignored|FAILED)(?:[, ].*)?$",line)
        if match:
            if target is None:
                raise ValueError("test identity has no target")
            identity = label+"::"+target+"::"+match[1]
            if identity in outcomes:
                raise ValueError("duplicate upstream test identity")
            outcomes[identity]={"ok":"passed","ignored":"skipped","FAILED":"failed"}[match[2]]
    if command[1]=="check":
        outcomes[label+"::compile-feature"]="passed" if run.returncode==0 else "failed"
report = {"passed":bool(outcomes) and not any(exits) and
          all(v in ("passed","skipped") for v in outcomes.values()),
          "commands":commands,"outcomes":outcomes,"exit_codes":exits,
          "limitations":"Pinned Linux Rust1.93.1; not upstream nightly/Windows/cross-target CI. Supported default/feature and alloc checks executed; identity/outcomes bound by original judge ledger."}
(ROOT/"upstream.json").write_text(json.dumps(report,indent=2)+"\n")
raise SystemExit(0 if report["passed"] else 1)
