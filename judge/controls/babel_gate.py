"""Transport the pinned delivery's identity gates through the shared executor."""
import json
import os
from pathlib import Path
import shutil
import sys
from babel_contract import archived_passed


def copy(source, target):
    shutil.copytree(source, target, symlinks=True)


def identities(report, root):
    rows = []
    for item in report["testResults"]:
        name = Path(item["name"]).relative_to(root).as_posix()
        for test in item["assertionResults"]:
            rows.append({"file": name, "name": json.dumps(
                [*test.get("ancestorTitles", []), test.get("title", test["fullName"])],
                ensure_ascii=False), "status": test["status"]})
    return sorted(rows, key=lambda row: (row["file"], row["name"], row["status"]))


group = sys.argv[1]
compiler = Path("/app/build/runtime")
base = Path("/work/scratch/local-build")
base.parent.mkdir(parents=True, exist_ok=True)
(base / "source").mkdir(parents=True)
copy("/app/deps/main", base / "source/babel")
copy("/app/deps/legacy", base / "source/legacy")
for name in json.loads(Path("/app/metadata/build-files.json").read_text()):
    destination = base / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes((Path("/app/babel") / name).read_bytes())

# The main checker tests generated production modules, not stale baseline lib/.
for row in json.loads(Path("/app/metadata/main-packages.json").read_text()):
    package = base / "source/babel/packages" / Path(row["source"]).name
    built = compiler / "node_modules" / row["package"]
    if (built / "lib").is_dir():
        shutil.rmtree(package / "lib", ignore_errors=True)
        copy(built / "lib", package / "lib")
    if row["package"] == "@babel/cli":
        for path in (built / "bin").glob("*.js"):
            shutil.copyfile(path, package / "bin" / path.name)

Path("/work/source").mkdir(exist_ok=True)
if group in ("main", "polyfills", "preset-modules"):
    # This trusted checker is extracted and authenticated by environment/prepare.py.
    # Its expected identity/outcome ledger remains unchanged and read-only.
    source = Path("/app/workload/driver/check_upstream.py").read_text()
    source = source.replace("--runInBand", "--maxWorkers=8").replace("--maxWorkers=2", "--maxWorkers=8")
    if os.environ.get("JUDGE_FAIL_FAST") == "1":
        source = source.replace("'--ci'", "'--ci', '--bail=1'")
    sys.argv = ["check_upstream.py", group, str(compiler)]
    error = None
    try:
        exec(compile(source, "pinned_babel_check_upstream.py", "exec"), {"__name__": "__main__"})
    except Exception as failure:
        error = str(failure)
    report_name = {"main": "main-tests", "polyfills": "polyfills-tests",
                   "preset-modules": "preset-modules-tests"}[group]
    root = {"main": Path("/work/source/babel"), "polyfills": Path("/work/polyfills"),
            "preset-modules": Path("/work/preset-modules")}[group]
    report = json.loads((Path("/work/scratch/upstream-reports") / (report_name + ".json")).read_text())
    outcomes = identities(report, root)
    passed = bool(error is None and report["success"] and not report["numFailedTests"])
    checker_exit_code = 0 if error is None else 1
else:
    if group not in ("historical120", "target117"):
        raise ValueError("Unknown Babel identity gate")
    root = Path("/work/archive")
    work, out = root / "work", root / "out"
    work.mkdir(parents=True)
    out.mkdir()
    source = Path("/app/workload/driver/archived_replay.py").read_text()
    source = source.replace("/work", str(work))
    for old, new in [
        ("/assets", "/app/deps"), ("/policy.json", "/app/metadata/build-policy.json"),
        ("/main-packages.json", "/app/metadata/main-packages.json"),
        ("/candidate-source", "/app/babel"), ("/compiler", str(compiler)),
        ("/contract.json", "/app/metadata/archived-contract.json"), ("/out", str(out)),
    ]:
        source = source.replace(old, new)
    namespace = {"__name__": "archived_gate"}
    exec(compile(source, "pinned_archived_replay.py", "exec"), namespace)
    code = namespace["inside"](group)
    result = json.loads((out / "result.json").read_text())
    # The target117 arm has known baseline failures: compare identities/statuses,
    # never convert those eight historical failures into an empty passing suite.
    passed = archived_passed(group, code, result)
    checker_exit_code = result["exit_code"]
    report = json.loads((out / "archived-tests.json").read_text())
    # Preserve full status/name identity set, normalizing only the fixed mount prefix.
    outcomes = sorted([{"file": row["name"].replace(str(work), "/work"), "tests": sorted([
        {"name": test["fullName"], "status": test["status"]} for test in row["assertionResults"]],
        key=lambda test: (test["name"], test["status"]))}
        for row in report["testResults"]], key=lambda row: row["file"])

counts = {"passed": report["numPassedTests"], "failed": report["numFailedTests"],
          "skipped": report["numPendingTests"], "identities": report["numTotalTests"]}
Path("/scratch/upstream.json").write_text(json.dumps({
    "passed": passed, "outcomes": outcomes, "counts": counts, "checker_exit_code": checker_exit_code}))
raise SystemExit(0 if passed else 1)
