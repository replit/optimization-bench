import json
from pathlib import Path
import subprocess
import sys
case = json.loads(Path(sys.argv[1]).read_text())
if case.get("kind") == "stateful":
    subprocess.run(["cc", "-I/app/build/project/src", "/verifier-differential/state.c",
                    "-L/app/build/project/.libs", "-ljq",
                    "-Wl,-rpath-link,/app/build/project/vendor/oniguruma/src/.libs",
                    "-o", "/scratch/state"], check=True, capture_output=True)
    p = subprocess.run(["/scratch/state"], capture_output=True, env={
        "LD_LIBRARY_PATH": "/app/build/project/.libs:/app/build/project/vendor/oniguruma/src/.libs"})
    print(p.stdout.decode())
    raise SystemExit(p.returncode)
results = []
for _ in range(case.get("repeat", 1)):
    p = subprocess.run(["/app/build/project/jq", "-c",
                        'walk(if type == "object" then del(.description) else . end)'],
                       input=case["raw"].encode(), capture_output=True)
    results.append({"code": p.returncode, "stdout": p.stdout.decode(errors="replace"),
                    "stderr": p.stderr.decode(errors="replace")})
print(json.dumps(results, ensure_ascii=False))
