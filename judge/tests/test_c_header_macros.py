"""Real compiler controls for macro-exported API, metadata, and origin filtering."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from judge import interfaces
from judge import c_preprocessor


@unittest.skipUnless(shutil.which("cc") and shutil.which("c++"), "C/C++ compilers required")
class MacroHeaderControls(unittest.TestCase):
    def gate(self, source, language="c", extra=None):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.task, self.baseline, self.candidate = [root / n for n in ("task", "base", "candidate")]
        (self.task / "tests").mkdir(parents=True)
        self.baseline.mkdir(); self.candidate.mkdir()
        self.config = {"kind": "declarations", "language": language, "files": ["include/api.h"]}
        if extra:
            self.config.update(extra)
        (self.task / "tests/judge.json").write_text(json.dumps({"public_interface": self.config}))
        for build in (self.baseline, self.candidate):
            (build / "include").mkdir()
            (build / "include/api.h").write_text(source)
        self.patch = patch.object(interfaces, "docker_c_preprocess",
                                 side_effect=lambda root, cfg, image, cpuset: c_preprocessor.preprocess(root, cfg))
        self.patch.start(); self.addCleanup(self.patch.stop)

    def check(self, changed):
        (self.candidate / "include/api.h").write_text(changed)
        return interfaces.check(self.task, self.baseline, self.candidate, "fixture", "0")

    def test_wrapped_removal_required_parameter_addition_and_positive_controls(self):
        for macro, prototype in [
            ("#define PNG_EXPORT(n, type, name, args) extern type name args\n"
             "typedef unsigned int png_uint_32;\n"
             "typedef const struct png_struct_def *png_const_structrp;\n",
             "PNG_EXPORT(123, png_uint_32, png_get_x_pixels_per_meter, (png_const_structrp png_ptr));"),
            ("#define ZEXTERN extern\n#define ZEXPORT\n#define OF(args) args\n",
             "ZEXTERN int ZEXPORT inflate OF((void *stream));"),
        ]:
            with self.subTest(macro=macro):
                source = macro + "\nint keep(void);\n" + prototype + "\n"
                self.gate(source)
                self.assertTrue(self.check(source)["passed"])
                for changed in (source.replace(prototype, ""),
                                source.replace("png_const_structrp png_ptr", "png_const_structrp png_ptr, int required")
                                      if "PNG_EXPORT" in source else
                                source.replace("void *stream", "void *stream, int required"),
                                source.replace("png_uint_32, png_get", "double, png_get")
                                      if "PNG_EXPORT" in source else
                                source.replace("int ZEXPORT inflate", "long ZEXPORT inflate")):
                    with self.assertRaises(interfaces.InterfaceBroken):
                        self.check(changed)
                for changed in (source + "\nint added(double x);\n",
                                "/* a comment */\n" + source.replace("int keep", "int   keep"),
                                source.replace("void *", "void    *")):
                    self.assertTrue(self.check(changed)["passed"])
                report = self.check(source)["components"][0]
                self.assertEqual(report["declaration_extraction"]["baseline"]["mode"], "preprocessed")
                self.patch.stop()

    def test_transitive_and_system_declarations_are_not_public(self):
        source = '#include "private.h"\n#include <stddef.h>\nint public_call(size_t n);\n'
        self.gate(source)
        for build in (self.baseline, self.candidate):
            (build / "include/private.h").write_text("int transitive(int x);\n")
        surface = interfaces.declarations(self.baseline, self.config)
        self.assertTrue(any("public_call" in k for k in surface))
        self.assertFalse(any("transitive" in k or "max_align_t" in k for k in surface))
        (self.candidate / "include/private.h").write_text("int completely_changed(double x);\n")
        self.assertTrue(self.check(source)["passed"])

    def test_declared_public_include_root_follows_its_transitive_exports(self):
        source = '#include "public/more.h"\n#include <stddef.h>\nint public_call(size_t n);\n'
        self.gate(source, extra={"public_include_roots": ["include/public"]})
        for build in (self.baseline, self.candidate):
            (build / "include/public").mkdir()
            (build / "include/public/more.h").write_text("int transitive_public(int x);\n")
        surface = interfaces.declarations(self.baseline, self.config)
        self.assertTrue(any("transitive_public" in key for key in surface))
        self.assertFalse(any("max_align_t" in key for key in surface))
        (self.candidate / "include/public/more.h").write_text("int changed_public(int x);\n")
        with self.assertRaises(interfaces.InterfaceBroken):
            self.check(source)

    def test_build_compile_database_includes_defines_and_linemarkers(self):
        source = '#include "config.h"\n#ifdef EXPOSE\nWRAP(long, visible, (int x));\n#endif\n'
        self.gate(source)
        for build in (self.baseline, self.candidate):
            (build / "generated").mkdir()
            (build / "generated/config.h").write_text("#define WRAP(t,n,a) extern t n a\n")
            (build / "compile_commands.json").write_text(json.dumps([{
                "directory": str(build), "file": str(build / "include/impl.c"),
                "arguments": ["cc", "-Igenerated", "-DEXPOSE=1", "-c", "include/impl.c", "-o", "x.o"]}]))
        self.assertTrue(self.check(source)["passed"])
        with self.assertRaises(interfaces.InterfaceBroken):
            self.check(source.replace("(int x)", "(int x, int required)"))

    def test_cpp_templates_classes_and_inline_bodies(self):
        source = ("#define API(t,n,a) t n a\nnamespace pkg { API(int, run, (int x));\n"
                  "struct S { int field; int method(int x) { return x+1; } }; }\n")
        self.gate(source, language="cpp")
        self.assertTrue(self.check(source.replace("return x+1", "return x+2"))["passed"])
        for changed in (source.replace("int field", "long field"),
                        source.replace("int method(int x)", "int method(int x, int required)"),
                        source.replace("API(int, run, (int x));", "")):
            with self.assertRaises(interfaces.InterfaceBroken):
                self.check(changed)

    def test_cmake_ninja_metadata_paths_are_relocated(self):
        source = '#include "generated.h"\n#ifdef PUBLIC_ENABLED\nEXPORT(int, visible, (int x));\n#endif\n'
        self.gate(source)
        for build in (self.baseline, self.candidate):
            (build / "cmake").mkdir()
            (build / "project").mkdir()
            (build / "generated").mkdir()
            (build / "generated/generated.h").write_text("#define EXPORT(t,n,a) t n a\n")
            (build / "cmake/CMakeCache.txt").write_text(
                "CMAKE_CACHEFILE_DIR:INTERNAL=/app/original/cmake\n"
                "CMAKE_C_COMPILER:FILEPATH=" + shutil.which("cc") + "\n")
            (build / "cmake/build.ninja").write_text(
                "build cmake/visible.o: C_COMPILER /app/original/project/visible.c\n"
                "  DEFINES = -DPUBLIC_ENABLED=1\n"
                "  INCLUDES = -I/app/original/generated\n")
        report = self.check(source)["components"][0]
        detail = report["declaration_extraction"]["candidate"]
        self.assertIn("cmake/build.ninja", detail["build_metadata"])
        self.assertTrue(any(str(self.candidate / "generated") in flag
                            for flag in detail["commands"][0]["flags"]))
        with self.assertRaises(interfaces.InterfaceBroken):
            self.check(source.replace("(int x)", "(long x)"))

    def test_makefile_include_and_define_expansion(self):
        source = '#include "generated.h"\n#ifdef PUBLIC_ENABLED\nEXPORT(int, visible, (int x));\n#endif\n'
        self.gate(source)
        for build in (self.baseline, self.candidate):
            (build / "generated").mkdir()
            (build / "generated/generated.h").write_text("#define EXPORT(t,n,a) extern t n a\n")
            (build / "Makefile").write_text(
                "CC = cc\nLOCAL = -DPUBLIC_ENABLED=1\nCPPFLAGS = $(LOCAL) -Igenerated\n")
        self.assertTrue(self.check(source)["passed"])
        with self.assertRaises(interfaces.InterfaceBroken):
            self.check(source.replace("visible", "renamed"))

    def test_no_compiler_fallback_is_explicit_but_bad_headers_fail_closed(self):
        self.gate("int keep(void);\n")
        with patch.object(c_preprocessor.shutil, "which", return_value=None):
            report = self.check("int keep(void);\n")["components"][0]
        self.assertEqual(report["declaration_extraction"]["baseline"]["mode"], "raw_unavailable")
        with self.assertRaises(interfaces.InterfaceBroken):
            self.check('#error deliberate invalid header\nint keep(void);\n')

    def test_umbrella_visits_subheaders_without_direct_reinclude(self):
        source = '#define IN_PUBLIC_API 1\n#include "detail/sub.h"\n#undef IN_PUBLIC_API\n'
        self.gate(source, extra={"files": ["include/*.h", "include/detail/*.h"]})
        for build in (self.baseline, self.candidate):
            (build / "include/detail").mkdir()
            (build / "include/detail/sub.h").write_text(
                '#ifndef IN_PUBLIC_API\n#error "include the umbrella"\n#endif\n'
                "int visible(int x);\n")
        self.assertTrue(self.check(source)["passed"])
        (self.candidate / "include/detail/sub.h").write_text(
            '#ifndef IN_PUBLIC_API\n#error "include the umbrella"\n#endif\n'
            "int visible(int x, int required);\n")
        with self.assertRaises(interfaces.InterfaceBroken):
            self.check(source)

    def test_candidate_cannot_downgrade_to_raw_or_hide_missing_dependencies(self):
        source = "int keep(void);\n"
        self.gate(source)
        def compiler(root, cfg, image, cpuset):
            if root == self.candidate:
                raise c_preprocessor.Unavailable("deliberate fixture compiler absence")
            return c_preprocessor.preprocess(root, cfg)
        with patch.object(interfaces, "docker_c_preprocess", side_effect=compiler):
            with self.assertRaises(interfaces.InterfaceBroken) as caught:
                self.check(source)
        self.assertIn("downgrade", caught.exception.report["components"][0]["candidate_introspection_error"])
        with self.assertRaises(interfaces.InterfaceBroken) as caught:
            self.check('#include "missing_private_dependency.h"\n' + source)
        self.assertIn("preprocessing failed", caught.exception.report["components"][0]["candidate_introspection_error"])

    def test_preprocessing_cannot_write_outputs_or_load_plugins(self):
        flags = c_preprocessor.cpp_flags(
            ["-DAPI=extern", "-I", "include", "-o", "/tmp/forbidden", "-fplugin=evil.so",
             "-Xclang", "-load", "-MF", "depfile", "-c", "source.c"], "/build", "c")
        self.assertEqual(flags, ["-DAPI=extern", "-I", "/build/include"])

    def test_missing_external_sdk_fallback_is_per_header_and_pristine_relative(self):
        source = "#define API(t,n,a) extern t n a\nAPI(int, visible, (int x));\n"
        self.gate(source, extra={"files": ["include/*.h"]})
        sdk = "#include <AbsentVendorSDK/core.h>\nint sdk_call(int x);\n"
        for build in (self.baseline, self.candidate):
            (build / "include/sdk.h").write_text(sdk)
        report = self.check(source)["components"][0]
        details = report["declaration_extraction"]
        for side in ("baseline", "candidate"):
            self.assertEqual(details[side]["mode"], "preprocessed+raw-unpreprocessable")
            self.assertEqual(details[side]["headers"]["include/api.h"]["mode"], "preprocessed")
            fallback = details[side]["headers"]["include/sdk.h"]
            self.assertEqual(fallback["mode"], "raw-unpreprocessable")
            self.assertIn("AbsentVendorSDK/core.h", fallback["error"])
        with self.assertRaises(interfaces.InterfaceBroken):
            self.check(source.replace("(int x)", "(int x, int required)"))
        self.check(source)
        for changed in (sdk.replace("int sdk_call(int x);", ""),
                        sdk.replace("(int x)", "(int x, int required)"),
                        sdk.replace("#include <AbsentVendorSDK/core.h>\n", "")):
            (self.candidate / "include/sdk.h").write_text(changed)
            with self.assertRaises(interfaces.InterfaceBroken):
                self.check(source)
        (self.candidate / "include/sdk.h").write_text("/* comment */\n" + sdk + "int added(void);\n")
        self.assertTrue(self.check(source)["passed"])
        (self.candidate / "include/new.h").write_text(sdk)
        with self.assertRaises(interfaces.InterfaceBroken) as caught:
            self.check(source)
        self.assertIn("Candidate-only", caught.exception.report["components"][0]["candidate_introspection_error"])

    def test_missing_task_namespace_is_not_an_external_sdk(self):
        source = "#include <owned/missing.h>\nint keep(void);\n"
        self.gate("int keep(void);\n")
        (self.candidate / "include/owned").mkdir()
        with self.assertRaises(interfaces.InterfaceBroken) as caught:
            self.check(source)
        self.assertIn("preprocessing failed", caught.exception.report["components"][0]["candidate_introspection_error"])

    def test_all_headers_can_use_pristine_sdk_raw_fallback(self):
        source = "#include <AbsentVendorSDK/core.h>\nint keep(void);\n"
        self.gate(source)
        self.assertTrue(self.check(source)["passed"])
        with self.assertRaises(interfaces.InterfaceBroken):
            self.check(source.replace("keep(void)", "keep(int required)"))
