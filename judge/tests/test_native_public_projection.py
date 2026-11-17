from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from judge import interfaces
from judge.native_surface import project


PRIVATE = "_ZN6charls12scan_decoder8end_scanEv"


@unittest.skipUnless(shutil.which("c++") and shutil.which("nm") and shutil.which("c++filt"),
                     "native binutils/compiler fixture unavailable")
class NativeProjectionTests(unittest.TestCase):
    def symbols(self):
        source = r"""
namespace charls {
struct scan_decoder { void end_scan() {} };
__attribute__((weak)) int public_weak() { return 7; }
int strong() { scan_decoder d; d.end_scan(); return 1; }
}
extern "C" __attribute__((weak)) int public_c_weak() { return 2; }
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fixture.cpp").write_text(source)
            subprocess.run(["c++", "-O0", "-c", str(root / "fixture.cpp"), "-o", str(root / "fixture.o")],
                           check=True, capture_output=True)
            return interfaces.symbols(root / "fixture.o", False)

    def test_private_vague_copy_can_disappear_but_real_public_weak_stays(self):
        before = self.symbols()
        self.assertEqual(before[PRIVATE], "W")
        tokens = {"namespace", "charls", "int", "public_weak", "strong", "public_c_weak"}
        left, proof = project(before, dynamic=False, public_tokens=tokens, namespaces={"charls"})
        right, _ = project({key: value for key, value in before.items() if key != PRIVATE},
                           dynamic=False, public_tokens=tokens, namespaces={"charls"})
        self.assertTrue(interfaces.compare(left, right)["passed"])
        self.assertIn(PRIVATE, proof["ignored"])
        self.assertIn("public_c_weak", left)
        public = next(key for key in left if "public_weak" in key)
        without_public = dict(right)
        without_public.pop(public)
        self.assertFalse(interfaces.compare(left, without_public)["passed"])

    def test_dynamic_exports_and_missing_public_proof_never_drop_weak_symbols(self):
        values = self.symbols()
        self.assertEqual(project(values, dynamic=True, public_tokens=set())[0], values)
        self.assertEqual(project(values, dynamic=False, public_tokens=None)[0], values)

    def test_entity_declared_in_public_header_remains_protected(self):
        values = self.symbols()
        projected, _ = project(values, dynamic=False, public_tokens={"scan_decoder"}, namespaces={"charls"})
        self.assertIn(PRIVATE, projected)
        self.assertTrue(all(projected.get(key) == value for key, value in values.items()
                            if value not in ("W", "V")))

    def test_projection_version_salts_interface_identity(self):
        self.assertEqual(interfaces.PROJECTION_VERSION, "externally-visible-projection-v3")
