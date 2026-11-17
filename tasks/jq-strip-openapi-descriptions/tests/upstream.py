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
    if KIND == "ctags":
        # Read the original harness's own categorized inventories at exit.
        # This observer neither changes tests nor substitutes their outcomes.
        instrument = ROOT / "instrument"
        instrument.mkdir()
        (ROOT / "ctags-ledgers").mkdir()
        (instrument / "sitecustomize.py").write_text(r'''
import atexit,json,os,sys
def save():
    m=sys.modules.get("__main__")
    if m is None or not getattr(m,"__file__","").endswith("units.py"): return
    names=("L_PASSED","L_FIXED","L_FAILED_BY_STATUS","L_FAILED_BY_DIFF",
           "L_SKIPPED_BY_FEATURES","L_SKIPPED_BY_LANGUAGES","L_SKIPPED_BY_ILOOP",
           "L_KNOWN_BUGS","L_FAILED_BY_TIMEED_OUT","L_BROKEN_ARGS_CTAGS","L_VALGRIND")
    data={name:getattr(m,name,[]) for name in names}
    with open("/scratch/ctags-ledgers/%s.json"%os.getpid(),"w") as f: json.dump(data,f)
atexit.register(save)
''')
        os.environ["PYTHONPATH"] = str(instrument)
    run("upstream", ["make", "-j8", "check"], cwd)
    for report in sorted(cwd.rglob("*.trs")):
        if "test" not in report.name.lower() and KIND == "ctags":
            continue
        for line in report.read_text().splitlines():
            match = re.fullmatch(r":test-result:\s+(\S+)(?:\s+(.*))?", line)
            if match:
                status = {"PASS": "passed", "SKIP": "skipped", "XFAIL": "xfail"}.get(match[1], "failed")
                identity = report.relative_to(cwd).as_posix() + "::" + (match[2] or "test")
                if KIND == "jq":
                    identity = ("tests/" if report.parent == cwd / "tests" else "") + report.stem
                record(identity, status)
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
        for report in sorted((ROOT / "ctags-ledgers").glob("*.json")):
            data = json.loads(report.read_text())
            for group, identities in data.items():
                if group == "L_FIXED":
                    if identities:
                        raise ValueError("Known Ctags failures unexpectedly fixed")
                    continue
                status = ("passed" if group == "L_PASSED" else
                          "skipped" if group.startswith("L_SKIPPED") else
                          "xfail" if group == "L_KNOWN_BUGS" else "failed")
                for identity in identities:
                    record("units::" + identity, status)
        # Terminal-main cases print a complete atomic block for each test.
        log = (ROOT / "upstream.stdout").read_text(errors="replace")
        for block in re.split(r"\nTesting ", log)[1:]:
            name = block.splitlines()[0].strip()
            if " as " in name:  # Unit results are already inventoried above.
                continue
            first = block.split("\n\n", 1)[0]
            if re.search(r"\bskipped\b", first):
                record("tmain::" + name, "skipped")
            elif re.search(r"\b(?:failed|INTERNAL-ERROR)\b", first):
                record("tmain::" + name, "failed")
            else:
                # Each compare aspect must have a positive result in its block.
                if "passed" not in first:
                    raise ValueError("Unrecognized terminal-main result: " + name)
                record("tmain::" + name, "passed")
if not outcomes:
    raise RuntimeError("Upstream suite emitted no identity inventory")
(ROOT / "upstream.json").write_text(json.dumps({"passed": True, "outcomes": outcomes},
                                               indent=2, sort_keys=True) + "\n")
