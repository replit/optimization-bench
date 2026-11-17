"""Baseline commit pins bind canonical trees, identities, dates and messages."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
ENV = dict(os.environ, GIT_AUTHOR_NAME="optimbench", GIT_AUTHOR_EMAIL="optimbench@invalid",
           GIT_COMMITTER_NAME="optimbench", GIT_COMMITTER_EMAIL="optimbench@invalid",
           GIT_AUTHOR_DATE="2000-01-01T00:00:00+0000", GIT_COMMITTER_DATE="2000-01-01T00:00:00+0000")


class BaselineGitTests(unittest.TestCase):
    def test_all_metadata_pins_equal_the_declared_root_commit_payload(self):
        for task in json.loads((ROOT / "datasets/all.json").read_text())["tasks"]:
            with self.subTest(task=task):
                folder = ROOT / "tasks" / task
                pins = tomllib.loads((folder / "task.toml").read_text())["metadata"]["optimbench"]
                dockerfile = (folder / "environment/Dockerfile").read_text()
                match = re.search(r"""commit -qm ["']([^"']+)["']""", dockerfile)
                if match is None:
                    prepare = (folder / "environment/prepare.py").read_text()
                    prepared_message = re.search(r'''"commit", "-qm", "([^"]+)"''', prepare)
                    if prepared_message:
                        message = prepared_message.group(1)
                    else:
                        self.assertIn('"commit", "-qm", library + " (pinned baseline)"', prepare)
                        message = json.loads((folder / "environment/layout.json").read_text())["library"] + " (pinned baseline)"
                else:
                    message = match.group(1)
                body = (f'tree {pins["baseline_git_tree"]}\n'
                        'author optimbench <optimbench@invalid> 946684800 +0000\n'
                        'committer optimbench <optimbench@invalid> 946684800 +0000\n\n'
                        f'{message}\n').encode()
                digest = hashlib.sha1(b"commit " + str(len(body)).encode() + b"\0" + body).hexdigest()
                self.assertEqual(digest, pins["baseline_git_commit"])
                git_command = "git -C /app/sqlite" if task == "sqlite-distinct-plant-facets" else "git"
                self.assertIn(f'test "$({git_command} rev-parse HEAD)" = ' + digest, dockerfile)
                self.assertIn(f'test "$({git_command} rev-parse HEAD^{{tree}})" = ' + pins["baseline_git_tree"], dockerfile)

    def test_creation_order_and_mtimes_do_not_change_commit_but_modes_do(self):
        hashes = []
        with tempfile.TemporaryDirectory() as temporary:
            for index, order in enumerate((("a", "script"), ("script", "a"))):
                root = Path(temporary) / str(index)
                root.mkdir()
                subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", str(root)], check=True)
                for name in order:
                    path = root / name
                    path.write_text(name + "\n")
                    path.chmod(0o755 if name == "script" else 0o644)
                    os.utime(path, (1000 + index, 1000 + index))
                subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.filemode=true",
                                "-C", str(root), "add", "-A"], check=True)
                subprocess.run(["git", "-c", "commit.gpgsign=false", "-C", str(root),
                                "commit", "-qm", "deterministic fixture"], env=ENV, check=True)
                hashes.append(subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"]))
                before = subprocess.check_output(["git", "-C", str(root), "write-tree"])
                subprocess.run(["git", "-C", str(root), "update-index", "--chmod=-x", "script"], check=True)
                self.assertNotEqual(before, subprocess.check_output(["git", "-C", str(root), "write-tree"]))
        self.assertEqual(*hashes)
