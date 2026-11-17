#!/usr/bin/env python3
"""Verifier-only baseline/candidate identity collection; copied into tests/."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path("/scratch")
BUILD = ROOT / "gate-build"
KIND = sys.argv[1]
os.environ["PATH"] = "/usr/local/go/bin:" + os.environ["PATH"]
outcomes = {}


def run(name, argv, cwd):
    with (ROOT / (name + ".stdout")).open("wb") as stdout, (ROOT / (name + ".stderr")).open("wb") as stderr:
        result = subprocess.run(argv, cwd=cwd, stdout=stdout, stderr=stderr)
    if result.returncode:
        raise RuntimeError(name + " failed:\n" +
                           (ROOT / (name + ".stderr")).read_text(errors="replace")[-5000:] +
                           (ROOT / (name + ".stdout")).read_text(errors="replace")[-2000:])


def record(name, status):
    if name in outcomes:
        raise ValueError("Duplicate test identity: " + name)
    if status not in ("passed", "skipped", "xfail"):
        raise ValueError("Nonpassing upstream test: " + name + " " + status)
    outcomes[name] = status


run("build", ["sh", "/app/workload/build.sh", str(BUILD)], ROOT)
project = BUILD / "project"
if KIND in ("charls", "xz"):
    run("upstream", ["ctest", "--test-dir", str(BUILD / "cmake"),
                     "--output-on-failure", "-j8", "--output-junit", str(ROOT / "ctest.xml")], ROOT)
    for case in ET.parse(ROOT / "ctest.xml").iter("testcase"):
        status = "failed" if case.find("failure") is not None else "skipped" if case.find("skipped") is not None else "passed"
        record(case.get("classname", "") + "::" + case.attrib["name"], status)
elif KIND == "goldmark":
    groups = [("tagged", ["go", "test", "-json", "-count=1", "-tags=goldmark_v1_attribute", "."], project / "parser"),
              ("default", ["go", "test", "-json", "-count=1", "-tags=", "-coverprofile=coverage.out",
                "-coverpkg=github.com/yuin/goldmark/v2,github.com/yuin/goldmark/v2/ast,github.com/yuin/goldmark/v2/extension,github.com/yuin/goldmark/v2/extension/ast,github.com/yuin/goldmark/v2/parser,github.com/yuin/goldmark/v2/renderer,github.com/yuin/goldmark/v2/renderer/html,github.com/yuin/goldmark/v2/text,github.com/yuin/goldmark/v2/util", "./..."], project)]
    for group, command, cwd in groups:
        run(group, command, cwd)
        for line in (ROOT / (group + ".stdout")).read_text().splitlines():
            event = json.loads(line)
            if event.get("Test") and event["Action"] in ("pass", "skip", "fail"):
                record(group + "::" + event["Package"] + "::" + event["Test"],
                       {"pass": "passed", "skip": "skipped", "fail": "failed"}[event["Action"]])
else:
    cwd = BUILD / "lib-build" if KIND == "lame" else project
    run("upstream", ["make", "-j8", "check"], cwd)
    for report in sorted(cwd.rglob("*.trs")):
        if "test" not in report.name.lower() and KIND == "ctags":
            continue
        for line in report.read_text().splitlines():
            match = re.fullmatch(r":test-result:\s+(\S+)(?:\s+(.*))?", line)
            if match:
                status = {"PASS": "passed", "SKIP": "skipped", "XFAIL": "xfail"}.get(match[1], "failed")
                record(report.relative_to(cwd).as_posix() + "::" + (match[2] or "test"), status)
    if KIND == "jq":
        # The protected jq_test.c prints full numbered identities. A passing
        # harness run is necessary but not sufficient: retain each number/name.
        for log in sorted(cwd.rglob("*.log")):
            if log.name == "test-suite.log":
                continue
            for line in log.read_text(errors="replace").splitlines():
                match = re.match(r"^(Test #\d+: .+ at line number \d+)", line)
                if match:
                    record(log.stem + "::" + match[1], "passed")
    if KIND == "ctags":
        # units.py writes category/name outcome inventories under its build dir.
        # Collect these explicit inventories rather than treating exit 0 as a test.
        for report in sorted(cwd.rglob("*.txt")):
            if report.name not in ("passed.txt", "skipped.txt", "failed.txt", "expected-failed.txt"):
                continue
            status = {"passed.txt": "passed", "skipped.txt": "skipped",
                      "failed.txt": "failed", "expected-failed.txt": "xfail"}[report.name]
            for line in report.read_text().splitlines():
                if line.strip():
                    record(report.relative_to(cwd).parent.as_posix() + "::" + line.strip(), status)
if not outcomes:
    raise RuntimeError("Upstream suite emitted no identity inventory")
(ROOT / "upstream.json").write_text(json.dumps({"passed": True, "outcomes": outcomes},
                                               indent=2, sort_keys=True) + "\n")
