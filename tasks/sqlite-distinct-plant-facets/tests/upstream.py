"""Run an explicit unchanged upstream selection and retain individual TCL IDs."""
import json
from collections import Counter
from pathlib import Path
import re
import subprocess

source = Path("/app/build/source")
fixture = Path("/app/build/objects/testfixture")
tests = ["select1.test", "select2.test", "where.test", "where2.test", "func.test"]
tests += ["ext/fts5/test/" + name for name in
          ("fts5simple.test", "fts5query.test", "fts5rank.test",
           "fts5unicode.test", "fts5content.test")]
# Suite scope change: the build enables SQLITE_ENABLE_RTREE (geonames-rtree workload), so the
# unchanged upstream R*Tree suite joins the gate. Geopoly is not enabled.
tests += ["ext/rtree/" + name for name in
          ("rtree1.test", "rtree2.test", "rtree3.test", "rtree4.test", "rtree5.test", "rtree6.test",
           "rtree7.test", "rtree8.test", "rtree9.test", "rtreeA.test", "rtreeB.test", "rtreeC.test",
           "rtreeD.test", "rtreeE.test", "rtreeF.test", "rtreeG.test", "rtreeH.test", "rtreeI.test",
           "rtreeJ.test", "rtreecheck.test", "rtreecirc.test", "rtreeconnect.test", "rtreedoc.test",
           "rtreedoc2.test", "rtreedoc3.test", "rtreefuzz001.test", "tkt3363.test")]
# Fault-injection loops (malloc_common.tcl's do_faultsim_test/do_malloc_test) name each step
# after its injection point, e.g. rtree3-3a-oom-persistent.1302. How many points a loop visits
# depends on the build's allocation pattern, so two builds of identical source report different
# step suffixes. Every step must still pass; the identity keeps the loop but not its step numbers.
fault_step = re.compile(r"^(.+-(?:oom|malloc|ioerr)-(?:persistent|transient))\.\d+$")
outcomes = []
for filename in tests:
    path = source / filename if filename.startswith("ext/") else source / "test" / filename
    log_name = Path(filename).name
    p = subprocess.run([str(fixture), str(path)],
                       cwd="/scratch", capture_output=True, text=True, timeout=1200)
    Path("/scratch/" + log_name + ".stdout").write_text(p.stdout)
    Path("/scratch/" + log_name + ".stderr").write_text(p.stderr)
    rows = re.findall(r"(?m)^(\S+)\.\.\.\s*(Ok|FAILED|Error)\s*$", p.stdout)
    if p.returncode or not rows or any(status != "Ok" for _, status in rows):
        raise RuntimeError("Original upstream selection failed: " + filename + "\n" +
                           p.stdout[-4000:] + p.stderr[-2000:])
    # Original TCL files reuse a handful of names. Preserve every execution,
    # distinguishing occurrences rather than silently collapsing them.
    seen = Counter()
    fault_loops = set()
    for name, _ in rows:
        loop = fault_step.match(name)
        if loop:
            if loop.group(1) in fault_loops:
                continue
            fault_loops.add(loop.group(1))
            name = loop.group(1) + ".all-fault-steps"
        seen[name] += 1
        outcomes.append({"identity": filename + "::" + name + "::occurrence-" + str(seen[name]),
                         "status": "passed"})
assert len({row["identity"] for row in outcomes}) == len(outcomes)
Path("/scratch/upstream.json").write_text(json.dumps({
    "passed": True, "tests": outcomes,
    "identity_policy": "filename::original-test-name::occurrence-N; all original executions retained, including reused names, "
                       "except that each fault-injection loop's build-dependent steps (<loop>.N) share one <loop>.all-fault-steps identity; "
                       "every step must still pass"
}, indent=2))
