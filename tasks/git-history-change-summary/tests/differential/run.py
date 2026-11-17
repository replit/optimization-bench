#!/usr/bin/env python3
"""Fresh CLI process and directory per atomic observation; complete streams."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

case = json.loads(Path(sys.argv[1]).read_text())
out = []
for index, test in enumerate(case["tests"]):
    root = Path("/scratch") / f"atomic-{index}"
    root.mkdir()
    for name in ("home", "xdg", "cache", "tmp"):
        (root / name).mkdir()
    (root / "global-config").write_bytes(b"")
    (root / "system-config").write_bytes(b"")
    for name in ("a", "b", "base"):
        if name in test:
            (root / name).write_bytes(test[name].encode("utf-8"))
    argv = ["/app/build/project/git"]
    if test["kind"] == "diff":
        argv += ["diff", "--no-index", "--no-ext-diff", "--color=never",
                 *test["options"], "--", "a", "b"]
    elif test["kind"] == "merge":
        argv += ["merge-file", "-p", *test["options"], "a", "base", "b"]
    else:
        raise ValueError("Unknown protected family")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    env.update({"LC_ALL": "C.UTF-8", "TZ": "UTC", "HOME": str(root/"home"),
                "XDG_CONFIG_HOME": str(root/"xdg"), "XDG_CACHE_HOME": str(root/"cache"),
                "TMPDIR": str(root/"tmp"), "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": str(root/"global-config"),
                "GIT_CONFIG_SYSTEM": str(root/"system-config"),
                "GIT_ATTR_NOSYSTEM": "1", "GIT_PAGER": "cat"})
    result = subprocess.run(argv, cwd=root, env=env, capture_output=True, timeout=20)
    out.append({"family": test["family"], "atom": test["atom"],
                "exit_code": result.returncode,
                "stdout_base64": base64.b64encode(result.stdout).decode(),
                "stderr_base64": base64.b64encode(result.stderr).decode()})
print(json.dumps({"family": case["family"], "observations": out}, sort_keys=True))
