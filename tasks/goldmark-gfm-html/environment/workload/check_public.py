import json,sys
from pathlib import Path
m=json.loads(Path("/app/workload/inputs/manifest.json").read_text())
want={d["path"]:(d["expect_len"],d["expect_sha"]) for batches in m["splits"].values() for b in batches for d in b["docs"]}
got={r[0]:(int(r[1]),r[2]) for r in (l.split() for l in Path(sys.argv[1]).read_text().splitlines())}
assert got==want, "Pinned public baseline results differ"
