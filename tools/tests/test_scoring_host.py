import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import os
import subprocess
from unittest.mock import patch

from tools import scoring_host as host


class NetworkModel:
    def __init__(self, ipv6=False, mtu=1500):
        self.ipv6, self.mtu, self.rules, self.mutations = ipv6, mtu, set(), []

    def __call__(self, argv, **kwargs):
        if argv[:3] == ["docker", "network", "ls"]:
            return 0, "abc123\n"
        if argv[:3] == ["docker", "network", "inspect"]:
            return 0, json.dumps([{"Name": "bridge", "Id": "abc123", "EnableIPv6": self.ipv6, "Options": {}}])
        if argv[:4] == ["ip", "-j", "link", "show"]:
            return 0, json.dumps([{"mtu": self.mtu}])
        if argv[:3] == ["ip", "link", "set"]:
            self.mutations.append(argv)
            self.mtu = int(argv[-1])
            return 0, ""
        operation = "-C" if "-C" in argv else "-A"
        identity = (argv[0], *argv[argv.index(operation) + 1:])
        if operation == "-C":
            return (0 if identity in self.rules else 1), ""
        self.rules.add(identity)
        self.mutations.append(argv)
        return 0, ""


class NetworkTests(unittest.TestCase):
    def test_fix_is_idempotent_and_covers_both_directions(self):
        model = NetworkModel()
        with patch.object(host, "run", model), patch.object(host, "local_daemon", return_value=True), patch.object(host.os, "geteuid", return_value=0):
            first = host.fix_network(1400, acknowledged=True)
            second = host.fix_network(1400, acknowledged=True)
        self.assertEqual(model.mtu, 1400)
        self.assertEqual(len(model.rules), 2)
        self.assertEqual(len(first["changes"]), 3)
        self.assertEqual(second["changes"], [])
        self.assertFalse(first["verified"]["packet_connectivity_tested"])
        self.assertFalse(any("restart" in argv for argv in model.mutations))

    def test_smaller_existing_mtu_never_increased_and_ipv6_included(self):
        model = NetworkModel(ipv6=True, mtu=1300)
        with patch.object(host, "run", model), patch.object(host, "local_daemon", return_value=True), patch.object(host.os, "geteuid", return_value=0):
            host.fix_network(1400, acknowledged=True)
        self.assertEqual(model.mtu, 1300)
        self.assertEqual(len(model.rules), 4)

    def test_permission_failure_not_misread_as_absent_rule(self):
        model = NetworkModel(mtu=1400)
        def run(argv, **kwargs):
            if argv[0] in ("iptables", "ip6tables"):
                return 2, ""
            return model(argv, **kwargs)
        with patch.object(host, "run", run), patch.object(host, "local_daemon", return_value=True), patch.object(host.os, "geteuid", return_value=0):
            with self.assertRaises(ValueError):
                host.fix_network(1400, acknowledged=True)
        self.assertEqual(model.mutations, [])

    def test_no_mutation_without_quiet_host_acknowledgement(self):
        with patch.object(host, "run") as run:
            with self.assertRaises(ValueError):
                host.fix_network()
            run.assert_not_called()

    def test_remote_daemon_not_modified_and_endpoint_secret_not_echoed(self):
        with patch.dict(host.os.environ, {"DOCKER_HOST": "tcp://user:secret@example.invalid:2376"}):
            self.assertFalse(host.local_daemon())
        with patch.object(host, "local_daemon", return_value=False), patch.object(host.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(ValueError, "remote Docker"):
                host.fix_network(acknowledged=True)

    def test_invalid_mtu_rejected(self):
        for value in (True, 1200, 10000, "1400"):
            with self.assertRaises(ValueError):
                host.network_check(value)


class InstallTests(unittest.TestCase):
    def test_binutils_probe_requires_known_demangling_and_unknown_preservation(self):
        def command(argv, **kwargs):
            if argv[-1] == "--version":
                return 0, "GNU binutils fixture"
            return 0, "foo()\nvtable for Foo\n_Zoptimbench_invalid_mangling\noptimbench_unmangled_symbol\n"
        with patch.object(host, "run", side_effect=command):
            self.assertEqual(host.binutils_check({"nm": "/usr/bin/nm", "c++filt": "/usr/bin/c++filt"}), [])

    def test_incompatible_demangler_has_binutils_repair(self):
        with patch.object(host, "run", side_effect=[(0, "GNU c++filt"), (0, "wrong\n\n")]):
            failures = host.binutils_check({"c++filt": "/usr/bin/c++filt"})
        self.assertEqual(failures[0]["check"], "c++filt-demangling")
        self.assertIn("install -y binutils", failures[0]["commands"][0])

    def test_preflight_does_not_accept_nm_without_cxxfilt(self):
        def command(argv, **kwargs):
            if argv[:2] == ["docker", "info"]:
                return 0, "2 cgroupfs"
            if argv[:3] == ["docker", "buildx", "version"]:
                return 0, "github.com/docker/buildx"
            if argv[:3] == ["git", "config", "--local"]:
                return 0, "git-lfs filter-process --skip"
            return 0, "GNU fixture"
        with patch.object(host, "interpreter_check", return_value=({}, [])), patch.object(host, "parser_check", return_value=({}, [])), patch("tools.judge_command_audit.check", return_value={}), patch.object(host.shutil, "which", side_effect=lambda name: None if name == "c++filt" else "/usr/bin/" + name), patch.object(host, "run", side_effect=command):
            with self.assertRaises(host.PreflightError) as error:
                host.scoring_preflight()
        missing = [row for row in error.exception.failures if row["check"] == "c++filt"]
        self.assertEqual(len(missing), 1)
        self.assertIn("install -y binutils", missing[0]["commands"][0])

    def test_clone_wrapper_skips_smudge_then_initializes_only_local_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "git.log"
            binary = root / "git"
            binary.write_text("#!/bin/sh\nprintf '%s|%s\\n' \"${GIT_LFS_SKIP_SMUDGE-unset}\" \"$*\" >> \"$TEST_GIT_LOG\"\n")
            binary.chmod(0o755)
            (root / "git-lfs").write_text("#!/bin/sh\nexit 0\n")
            (root / "git-lfs").chmod(0o755)
            result = subprocess.run(
                ["sh", str(host.ROOT / "tools/clone_verifier_checkout.sh"), "https://example.invalid/repo.git", "target"],
                env={**os.environ, "PATH": str(root) + os.pathsep + os.environ.get("PATH", ""),
                     "TEST_GIT_LOG": str(log)}, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            commands = log.read_text().splitlines()
            self.assertTrue(commands[0].startswith("1|clone -- "))
            self.assertIn("-C target lfs install --local --skip-smudge", commands[1])
            self.assertNotIn("pull", "\n".join(commands))

    def test_buildx_hash_verified_atomic_and_idempotent(self):
        data = b"synthetic buildx binary"
        checksum = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plugins/docker-buildx"
            with patch.object(host.platform, "system", return_value="Linux"), patch.object(host.platform, "machine", return_value="x86_64"), patch.dict(host.BUILD_X, {"x86_64": ("amd64", checksum)}), patch.object(host.urllib.request, "urlopen", return_value=io.BytesIO(data)) as urlopen:
                first = host.install_buildx(path)
                second = host.install_buildx(path)
            self.assertEqual(first["status"], "installed")
            self.assertEqual(second["status"], "already-installed")
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(path.stat().st_mode & 0o777, 0o755)
            self.assertEqual(urlopen.call_count, 1)

    def test_buildx_wrong_hash_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plugins/docker-buildx"
            with patch.object(host.platform, "system", return_value="Linux"), patch.object(host.platform, "machine", return_value="x86_64"), patch.object(host.urllib.request, "urlopen", return_value=io.BytesIO(b"wrong")):
                with self.assertRaisesRegex(ValueError, "SHA256"):
                    host.install_buildx(path)
            self.assertFalse(path.exists())
            self.assertEqual(list(path.parent.iterdir()), [])

    def test_existing_operator_plugin_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "docker-buildx"
            path.write_bytes(b"operator install")
            with self.assertRaisesRegex(ValueError, "retained"):
                host.install_buildx(path)
            self.assertEqual(path.read_bytes(), b"operator install")

    def test_system_python_and_missing_parsers_have_exact_commands(self):
        with patch.object(host.sys, "prefix", "/system"), patch.object(host.sys, "base_prefix", "/system"), patch.object(host.importlib, "import_module", side_effect=ImportError):
            _, failures = host.parser_check()
        self.assertTrue(failures)
        command = "\n".join(failures[0]["commands"])
        self.assertIn("--require-hashes --only-binary=:all:", command)
        self.assertIn("judge/requirements-interface.lock", command)
        self.assertIn("judge/requirements-host-registry.lock", command)
        self.assertIn("/opt/judge/bin/python", command)


if __name__ == "__main__":
    unittest.main()
