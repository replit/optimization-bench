"""Exercise the documented shell flow with the real public comparator."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from tools.update_catalog import agent_instructions, validate_neutral_prompt


class GoldmarkPublicCheckInstructionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.task = Path(__file__).resolve().parents[2]
        self.prompt = (self.task / "instruction.md").read_text()
        block = self.prompt.split("## Build and check\n", 1)[1].split("\n## ", 1)[0]
        self.commands = re.search(r"```sh\n(.*?)\n```", block, re.S).group(1)
        self.workload = self.root / "workload"
        (self.workload / "inputs").mkdir(parents=True)
        digest = hashlib.sha256(b"ok\n").hexdigest()
        manifest = {"splits": {"public": [{"docs": [
            {"path": "document", "expect_len": 3, "expect_sha": digest}
        ]}]}}
        (self.workload / "inputs/manifest.json").write_text(json.dumps(manifest))
        self.results = self.root / "public.tsv"
        self.results.write_text(f"document\t3\t{digest}\n")
        checker = (self.task / "environment/workload/check_public.py").read_text()
        (self.workload / "check_public.py").write_text(
            checker.replace("/app/workload", str(self.workload)))
        (self.workload / "build.sh").write_text(
            """#!/bin/sh
set -eu
[ "${BUILD_STATUS:-0}" = 0 ] || exit "$BUILD_STATUS"
test ! -e "$1"
mkdir -p "$1"
cat > "$1/run" <<'SH'
#!/bin/sh
cat "$PUBLIC_RESULTS"
exit "${RUN_STATUS:-0}"
SH
chmod +x "$1/run"
""")
        self.env = dict(os.environ, TMPDIR=str(self.root),
                        PUBLIC_RESULTS=str(self.results))
        self.env.pop("BUILD_STATUS", None)
        self.env.pop("RUN_STATUS", None)

    def run_commands(self, **env):
        return subprocess.run(
            ["sh", "-c", self.commands.replace("/app/workload", str(self.workload))],
            env={**self.env, **env}, capture_output=True, text=True, timeout=10)

    def test_public_check_can_be_repeated_with_fresh_build_directories(self):
        for _ in range(2):
            result = self.run_commands()
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(self.root.glob("*/build/run"))), 2)

    def test_mismatched_output_is_rejected(self):
        self.results.write_text(f"document\t3\t{'0' * 64}\n")
        result = self.run_commands()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Pinned public baseline results differ", result.stderr)

    def test_failed_build_stops_the_commands(self):
        result = self.run_commands(BUILD_STATUS="17")
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(list(self.root.glob("*/results.tsv")), [])

    def test_failed_runner_is_not_masked_by_matching_output(self):
        result = self.run_commands(RUN_STATUS="23")
        self.assertEqual(result.returncode, 23, result.stderr)

    def test_missing_lint_tools_are_explicit_without_waiving_upstream_tests(self):
        self.assertIn("`golangci-lint` and `gopls` are not installed", self.prompt)
        self.assertIn("That lint requirement does not apply to this task", self.prompt)
        self.assertIn("`cd /app/goldmark && make test`", self.prompt)
        self.assertEqual(agent_instructions(self.task), self.prompt)
        validate_neutral_prompt(self.task)
