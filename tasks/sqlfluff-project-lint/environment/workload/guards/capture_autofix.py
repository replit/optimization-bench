#!/usr/local/bin/python
"""Snapshot declared file-based operations after setup and before timing."""
from pathlib import Path
import sys


def main():
    scratch = Path("/scratch")
    fixer = (scratch / "autofix-before.json").is_file()
    alternate = (scratch / "alternate-before.json").is_file()
    if not fixer and not alternate:
        return
    import json
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from driver.autofix import capture, snapshot
    request = json.loads(Path(sys.argv[1]).read_text())
    steps = request["steps"]
    if fixer:
        expected = ["/usr/local/bin/python", "-m", "sqlfluff", "fix"]
        if alternate or len(steps) != 1 or steps[0]["argv"][:4] != expected:
            raise ValueError("Auto-fix preparation marker requires the declared CLI")
        capture(scratch)
    else:
        argv = steps[0]["argv"] if len(steps) == 1 else []
        lint = argv[:4] == ["/usr/local/bin/python", "-m", "sqlfluff", "lint"]
        tree = argv[:2] == ["/usr/local/bin/python", "/app/workload/driver/export_tree.py"]
        if not lint and not tree:
            raise ValueError("Alternate preparation marker requires its declared client")
        from driver.alternates import capture as capture_alternate
        capture_alternate(scratch, snapshot)


if __name__ == "__main__":
    main()
