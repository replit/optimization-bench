import json, random, subprocess, sys, os, collections
sys.path.insert(0, "/fuzz")
import adapter
CASE = f"/tmp/case-{os.getpid()}.json"
site_a, site_b, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
rng = random.Random(1729)
stats = collections.Counter(); diffs = []
for case in adapter.generate(rng, n):
    pre = adapter.precondition(case, None)
    if not pre["valid"]:
        stats["invalid"] += 1; continue
    open(CASE, "w").write(json.dumps(case))
    obs = []
    for site in (site_a, site_b):
        p = subprocess.run([sys.executable, "/fuzz/driver.py", site, CASE, "x"], capture_output=True, text=True, timeout=300)
        if p.returncode:
            obs.append({"termination": {"kind": "signal" if p.returncode < 0 else "exception", "code": p.returncode, "stderr": p.stderr[-300:]}, "outcome": {}, "effects": {}})
        else:
            obs.append(json.loads(p.stdout.strip().splitlines()[-1]))
    r = adapter.compare(obs[0], obs[1], case)
    stats["equal" if r["equal"] else "different"] += 1
    stats["term_" + obs[0]["termination"]["kind"]] += 1
    calls = obs[0].get("outcome", {}).get("calls", [])
    stats["calls_error" if any(c["status"] == "error" for c in calls) else "calls_ok"] += 1
    if not r["equal"]:
        diffs.append({"case": case, "diff": r["differences"][:3]})
print(json.dumps(dict(stats)))
for d in diffs[:5]: print(json.dumps(d)[:700])
