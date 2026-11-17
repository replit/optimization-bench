"""Static registration checks; runtime evidence is retained under receipts/."""
from pathlib import Path
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools/runner"))
from judge.fuzzing_generic import task_runners
from tools.fuzz_runners.header_matrix import dialect_flags, validate_manifest

TASK = ROOT / "tasks/bzip2-codec"


class Bzip2Registration(unittest.TestCase):
    def setUp(self):
        self.callbacks, self.module = task_runners(TASK)

    def test_library_callbacks_and_shared_requests_registered(self):
        self.assertEqual(sorted(self.callbacks), ["aliasing", "repeat_in_process", "stream_faults"])
        self.assertTrue(callable(self.module.CLI_CASES) and callable(self.module.HEADER_CONSUMERS))

    def test_precondition_quarantines_unbounded_or_unknown_cases(self):
        case = {"surface_id": "bzip2.c.bzlib.stream+buffer+stdio", "operation": "stream",
                "options": {"blocksize": 9, "workfactor": 30, "in_chunk": 1, "out_chunk": 7},
                "input": {"sha256": "0" * 64, "bytes": 10}}
        self.assertTrue(self.module.PRECONDITION(case, {})["valid"])
        for change in ({"operation": "bzopen"}, {"surface_id": "private"},
                       {"options": {"blocksize": 9, "in_chunk": 0}},
                       {"input": {"sha256": "0" * 64, "bytes": 1 << 30}}):
            self.assertFalse(self.module.PRECONDITION({**case, **change}, {})["valid"])
        init = {"surface_id": case["surface_id"], "operation": "compress_init",
                "options": {"blocksize": 10, "verbosity": 0, "workfactor": 30}, "input": None}
        self.assertTrue(self.module.PRECONDITION(init, {})["valid"])  # documented BZ_PARAM_ERROR input

    def test_header_manifest_is_valid_and_bound_to_pinned_flags(self):
        manifest = json.loads((TASK / "tests/fuzz/header-manifest.json").read_text())
        validate_manifest(manifest)
        self.assertEqual({cell["language"] for cell in manifest["cells"]}, {"c", "c++"})
        c = next(cell for cell in manifest["cells"] if cell["language"] == "c")
        self.assertEqual(c["support_basis"], "pinned-upstream-default-flags")
        self.assertEqual(c["headers"], ["source/bzlib.h"])
        cxx = next(cell for cell in manifest["cells"] if cell["language"] == "c++")
        self.assertIs(cxx["supported"], True)
        self.assertNotIn("support_status", cxx)
        self.assertEqual(cxx["compiler"], "g++")
        self.assertEqual(cxx["support_basis"], "pinned-upstream-default-flags")
        self.assertEqual(dialect_flags(cxx), [])  # no invented numeric portability minimum
        self.assertEqual(cxx["headers"], ["source/bzlib.h"])
        self.assertEqual(cxx["libraries"], ["source/libbz2.a"])
        self.assertEqual(len(cxx["translation_units"]), 2)
        self.assertTrue(all(tu["name"].endswith(".cpp") for tu in cxx["translation_units"]))
        self.assertIn("extern \"C\"", cxx["support_refs"][0]["quote"])

    def test_image_provides_the_registered_cpp_compiler(self):
        dockerfile = (TASK / "environment/Dockerfile").read_text()
        install = next(line for line in dockerfile.splitlines()
                       if "apt-get install" in line)
        self.assertIn("g++", install.split())
        self.assertIn("dpkg-query -W > /app/os-packages.lock", dockerfile)

    def test_alias_precondition_and_normalization_authority(self):
        self.assertTrue(self.module.PRECONDITION({"surface_id": "bzip2.c.bzlib.library-owned-pointers",
                                                  "operation": "read_get_unused_chain"}, {})["valid"])
        driver = (TASK / "tests/fuzz/rc_driver.py").read_text()
        self.assertIn("eventually to be compressed and written to the file", driver)
        self.assertIn("write_calls_accepted_before_error", driver)

    def test_probe_records_affinity_and_lifecycle(self):
        source = (TASK / "tests/fuzz/rc_probe.c").read_text()
        for token in ("sched_getaffinity", "execution_affinity", "/proc/self/task", "start_ticks"):
            self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
