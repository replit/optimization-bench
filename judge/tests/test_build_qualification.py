"""Pure ELF fixtures: code/layout, harmless debug and disclosed date differences."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from judge import build_qualification as qualification


def emit(path, text=b"\xc3", rodata=b"same data\0", debug=b"debug", offset=512):
    names = b"\0.text\0.rodata\0.note.gnu.build-id\0.debug_info\0.shstrtab\0"
    rows = [
        (b"", 0, 0, 0, 0, b""),
        (b".text", 1, 6, 0x1000, 256, text),
        (b".rodata", 1, 2, 0x2000, offset, rodata),
        (b".note.gnu.build-id", 7, 2, 0x3000, 768, b"build-id"),
        (b".debug_info", 1, 0, 0, 1024, debug),
        (b".shstrtab", 3, 0, 0, 1280, names),
    ]
    data = bytearray(2048 + 64 * len(rows))
    struct.pack_into("<16sHHIQQQIHHHHHH", data, 0,
                     b"\x7fELF\x02\x01\x01" + b"\0" * 9, 2, 62, 1, 0x1000,
                     0, 2048, 0, 64, 0, 0, 64, len(rows), 5)
    for index, (name, kind, flags, address, start, body) in enumerate(rows):
        if start:
            data[start:start + len(body)] = body
        name_index = names.index(name + b"\0") if name else 0
        struct.pack_into("<IIQQQQIIQQ", data, 2048 + index * 64,
                         name_index, kind, flags, address, start, len(body), 0, 0, 1, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


class QualificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.first, self.second = self.root / "first", self.root / "second"

    def test_whole_file_identical(self):
        emit(self.first)
        emit(self.second)
        result = qualification.compare(self.first, self.second)
        self.assertEqual(result["status"], "identical")
        self.assertEqual(result["first_sha256"], result["second_sha256"])

    def test_debug_only_difference_is_disclosed_not_a_code_change(self):
        emit(self.first, debug=b"debug path /original")
        emit(self.second, debug=b"debug path /new-path")
        result = qualification.compare(self.first, self.second)
        self.assertFalse(result["identical"])
        self.assertEqual(result["status"], "acceptable-with-disclosure")
        self.assertEqual(result["differing_alloc_sections"], [])

    def test_same_length_date_time_is_acceptable_only_without_layout_or_code_changes(self):
        emit(self.first, rodata=b"built Oct 25 2026 10:11:12\0")
        emit(self.second, rodata=b"built Oct 26 2026 13:14:15\0")
        result = qualification.compare(self.first, self.second)
        self.assertEqual(result["status"], "acceptable-with-disclosure")
        self.assertEqual(result["differing_alloc_sections"], [".rodata"])
        emit(self.second, text=b"\x90", rodata=b"built Oct 26 2026 13:14:15\0")
        self.assertEqual(qualification.compare(self.first, self.second)["status"], "flagged")
        emit(self.second, rodata=b"built Oct 26 2026 13:14:15\0", offset=520)
        self.assertEqual(qualification.compare(self.first, self.second)["status"], "flagged")

    def test_same_length_non_date_path_difference_is_flagged(self):
        emit(self.first, rodata=b"/app/arm0/path\0")
        emit(self.second, rodata=b"/app/arm1/path\0")
        self.assertEqual(qualification.compare(self.first, self.second)["status"], "flagged")

    def test_exported_programs_and_native_extensions_exclude_intermediates(self):
        emit(self.root / "build/prog")
        emit(self.root / "build/site/numpy/module.so")
        emit(self.root / "build/target/test")
        self.assertEqual(qualification.artifacts(self.root / "build"),
                         ["prog", "site/numpy/module.so"])

    def test_builds_pristine_twice_fresh_without_cache_seed_or_scoring(self):
        task, trusted = self.root / "task", self.root / "trusted"
        task.mkdir()
        (task / "release.json").write_text(json.dumps({"source_policy": {}}))
        def build(task, pristine, image, cpuset, output, source=None):
            self.assertEqual(source, trusted / "pristine")
            emit(output / "build/program")
        with patch.object(qualification, "image_id", return_value="image"), \
             patch.object(qualification.score, "assets", return_value=trusted), \
             patch.object(qualification.build, "build", side_effect=build) as builds, \
             patch.object(qualification.policy, "envelope", return_value={"files": {}}):
            result = qualification.qualify(task, "image", "0-7", self.root / "out", self.root / "cache")
        self.assertEqual(builds.call_count, 2)
        self.assertNotEqual(builds.call_args_list[0].args[4], builds.call_args_list[1].args[4])
        self.assertEqual(result["status"], "identical")
        self.assertFalse(result["claimable"])
        self.assertFalse(result["timing_performed"])
