from pathlib import Path
import unittest
from unittest.mock import patch
from tools.fuzz_runners import build_image

ROOT = Path(__file__).resolve().parents[2]


class OverlayRecipeTests(unittest.TestCase):
    def test_python_index_override_is_build_command_local(self):
        recipe = (ROOT / "tools/fuzz_runners/images/python.Dockerfile").read_text()
        self.assertIn("PIP_NO_INDEX=0 /opt/fuzz-python/bin/pip install --ignore-installed --require-hashes", recipe)
        self.assertNotIn("ENV PIP_NO_INDEX=0", recipe)
        self.assertIn("venv --system-site-packages", recipe)

    def test_native_distro_guard_precedes_apt_source_replacement(self):
        recipe = (ROOT / "tools/fuzz_runners/images/native.Dockerfile").read_text()
        self.assertLess(recipe.index('"$ID" != debian'), recipe.index("rm -f /etc/apt/sources.list"))

    def test_go_bootstrap_does_not_depend_on_tasks_offline_module_proxy(self):
        recipe = (ROOT / "tools/fuzz_runners/images/go.Dockerfile").read_text()
        self.assertIn("GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org go mod download", recipe)
        self.assertNotIn("ENV GOTOOLCHAIN=local GOPROXY=off", recipe)

    def test_rust_bootstrap_overrides_cargo_offline_for_locked_fetch_only(self):
        recipe = (ROOT / "tools/fuzz_runners/images/rust.Dockerfile").read_text()
        self.assertIn("CARGO_NET_OFFLINE=false cargo install", recipe)
        self.assertIn("CARGO_NET_OFFLINE=false cargo fetch --locked", recipe)
        self.assertIn("ENV CARGO_NET_OFFLINE=true", recipe)

    def test_npm_bootstrap_can_populate_locked_cache_for_offline_task_parent(self):
        recipe = (ROOT / "tools/fuzz_runners/images/javascript.Dockerfile").read_text()
        self.assertIn("npm_config_offline=false npm ci", recipe)
        self.assertNotIn("ENV npm_config_offline=false", recipe)

    def test_local_id_is_bound_to_same_daemon_not_used_as_from_repository(self):
        identity = "sha256:" + "a" * 64
        with patch.object(build_image.subprocess, "check_output", side_effect=[identity + "\n", identity + "\n"]), patch.object(build_image.subprocess, "run") as run:
            ref, env, record = build_image.local_parent(identity)
        self.assertEqual(ref, "optimbench-fuzz-parent:sha256-" + "a" * 64)
        self.assertEqual(env, {"DOCKER_BUILDKIT": "0"})
        self.assertEqual(record["requested"], identity)
        self.assertTrue(record["local_tag_retained"])
        run.assert_called_once()

    def test_local_tag_mismatch_is_rejected(self):
        with patch.object(build_image.subprocess, "check_output", side_effect=["sha256:" + "a" * 64, "sha256:" + "b" * 64]), patch.object(build_image.subprocess, "run"):
            with self.assertRaises(ValueError):
                build_image.local_parent("sha256:" + "a" * 64)

    def test_oci_manifest_route_does_not_require_local_tag(self):
        identity = "localhost:5000/task@sha256:" + "a" * 64
        ref, env, record = build_image.local_parent(identity)
        self.assertEqual(ref, identity)
        self.assertEqual(env, {})
        self.assertEqual(record["kind"], "OCI-manifest-reference")


if __name__ == "__main__":
    unittest.main()
