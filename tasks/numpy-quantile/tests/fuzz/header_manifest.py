"""Generate the numpy-quantile public-header consumer manifest from PINNED PRISTINE sources.

Run inside the pinned task image (pristine source at /app/numpy, pristine build at
/app/baseline-build). Support evidence is read and hashed before any candidate result.
Usage: python3 header_manifest.py <out.json>
"""
import hashlib
import json
from pathlib import Path
import sys

SRC = Path("/app/numpy")
BUILD = Path("/app/baseline-build")
INC = BUILD / "site/numpy/_core/include/numpy"


def ref(path, needle):
    raw = (SRC / path).read_bytes()
    text = raw.decode()
    line = next(l for l in text.splitlines() if needle in l)
    return {"path": f"numpy-2.5.4-sdist/{path}", "sha256": hashlib.sha256(raw).hexdigest(),
            "quote": line.strip()}


headers = sorted(str(p.relative_to(INC.parent)) for p in INC.rglob("*.h"))
c_default_refs = [
    ref("numpy/_core/tests/examples/limited_api/meson.build", "'limited_api.c',"),
    ref("numpy/_core/tests/examples/limited_api/meson.build", "project("),
    ref("numpy/_core/tests/examples/limited_api/limited_api.c", "#include"),
]
cxx_refs = [ref("numpy/_core/include/numpy/ndarraytypes.h", "#ifdef __cplusplus"),
            ref("numpy/_core/include/numpy/npy_math.h", "#ifdef __cplusplus")]
build_std_refs = [ref("meson.build", "'c_std=c11',"), ref("meson.build", "'cpp_std=c++17',")]

PY = ["-I/usr/local/include/python3.12"]
LINK = ["-L/usr/local/lib", "-lpython3.12", "-lm"]
INCLUDES = ["numpy/arrayobject.h", "numpy/ufuncobject.h", "numpy/npy_math.h", "numpy/halffloat.h"]
covered = sorted({"numpy/arrayobject.h", "numpy/ndarrayobject.h", "numpy/ndarraytypes.h",
                  "numpy/npy_common.h", "numpy/numpyconfig.h", "numpy/_numpyconfig.h",
                  "numpy/npy_endian.h", "numpy/npy_cpu.h", "numpy/utils.h",
                  "numpy/__multiarray_api.h", "numpy/npy_2_compat.h", "numpy/ufuncobject.h",
                  "numpy/__ufunc_api.h", "numpy/npy_math.h", "numpy/halffloat.h",
                  "numpy/_neighborhood_iterator_imp.h", "numpy/arrayscalars.h"} & set(headers))
TU1 = """#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define PY_ARRAY_UNIQUE_SYMBOL OPTIMBENCH_NPQ_ARRAY_API
#define PY_UFUNC_UNIQUE_SYMBOL OPTIMBENCH_NPQ_UFUNC_API
#define NPY_NO_DEPRECATED_API NPY_1_21_API_VERSION
#include <numpy/arrayobject.h>
#include <numpy/ufuncobject.h>
#include <numpy/npy_math.h>
#include <numpy/halffloat.h>
int consumer_two(void);
int main(void) {
    Py_Initialize();
    if (_import_array() < 0) { PyErr_Print(); return 2; }
    if (_import_umath() < 0) { PyErr_Print(); return 3; }
    npy_intp dims[1] = {3};
    PyObject *a = PyArray_SimpleNew(1, dims, NPY_DOUBLE);
    double h = npy_half_to_double(npy_double_to_half(1.5));
    int rc = (a != NULL && PyArray_NDIM((PyArrayObject *)a) == 1 && h == 1.5) ? consumer_two() : 4;
    Py_XDECREF(a);
    return rc;
}
"""
TU2 = """#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NO_IMPORT_ARRAY
#define NO_IMPORT_UFUNC
#define PY_ARRAY_UNIQUE_SYMBOL OPTIMBENCH_NPQ_ARRAY_API
#define PY_UFUNC_UNIQUE_SYMBOL OPTIMBENCH_NPQ_UFUNC_API
#define NPY_NO_DEPRECATED_API NPY_1_21_API_VERSION
#include <numpy/arrayobject.h>
#include <numpy/ufuncobject.h>
#include <numpy/npy_math.h>
#include <numpy/halffloat.h>
int consumer_two(void) {
    PyArray_Descr *d = PyArray_DescrFromType(NPY_FLOAT64);
    int ok = d != NULL && PyDataType_ELSIZE(d) == 8 && npy_isnan(NPY_NAN) && npy_isfinite(npy_nextafter(1.0, 2.0)) && npy_signbit(-0.0);
    Py_XDECREF(d);
    return ok ? 0 : 5;
}
"""
base = {"headers": ["site/numpy/_core/include/" + h for h in covered], "features": ["NPY_NO_DEPRECATED_API=NPY_1_21_API_VERSION",
                                         "PY_ARRAY_UNIQUE_SYMBOL/NO_IMPORT_ARRAY split"],
        "include_context": {"id": "python-extension-umbrella", "includes": INCLUDES},
        "include_dirs": ["site/numpy/_core/include"],
        "libraries": ["site/numpy/_core/lib/libnpymath.a"],
        "compile_flags": PY, "link_flags": LINK, "compiler": "gcc",
        "symbol_refs": ["PyArray_SimpleNew", "PyArray_DescrFromType", "_import_array",
                        "npy_half_to_double", "npy_double_to_half", "npy_nextafter"]}
XINC = ["numpy/arrayobject.h", "numpy/dtype_api.h", "numpy/npy_3kcompat.h", "numpy/npy_os.h",
        "numpy/npy_no_deprecated_api.h", "numpy/npy_2_complexcompat.h",
        "numpy/random/bitgen.h", "numpy/random/distributions.h", "numpy/random/libdivide.h"]
XHEAD = "".join("#include <%s>\n" % h for h in XINC)
XTU1 = ("#define PY_SSIZE_T_CLEAN\n#include <Python.h>\n#define PY_ARRAY_UNIQUE_SYMBOL OPTIMBENCH_NPQ_X_API\n"
        "#define NPY_NO_DEPRECATED_API NPY_1_21_API_VERSION\n" + XHEAD +
        "static uint64_t next64(void *st) { return ++*(uint64_t *)st * 0x9E3779B97F4A7C15ULL; }\n"
        "static uint32_t next32(void *st) { return (uint32_t)(next64(st) >> 32); }\n"
        "static double nextd(void *st) { return (next64(st) >> 11) * (1.0 / 9007199254740992.0); }\n"
        "int consumer_x_two(void);\n"
        "int main(void) {\n    uint64_t state = 1;\n    bitgen_t g = {&state, next64, next32, nextd, NULL};\n"
        "    double u = random_standard_uniform(&g);\n"
        "    if (!(u >= 0.0 && u < 1.0)) return 6;\n    Py_Initialize();\n"
        "    if (_import_array() < 0) { PyErr_Print(); return 2; }\n    return consumer_x_two();\n}\n")
XTU2 = ("#define PY_SSIZE_T_CLEAN\n#include <Python.h>\n#define NO_IMPORT_ARRAY\n"
        "#define PY_ARRAY_UNIQUE_SYMBOL OPTIMBENCH_NPQ_X_API\n#define NPY_NO_DEPRECATED_API NPY_1_21_API_VERSION\n" + XHEAD +
        "int consumer_x_two(void) {\n    npy_cdouble z;\n    npy_csetreal(&z, 1.0); npy_csetimag(&z, -2.0);\n"
        "    struct libdivide_s64_t d = libdivide_s64_gen(7);\n"
        "    PyArray_Descr *f8 = PyArray_DescrFromType(NPY_FLOAT64);\n"
        "    PyTypeObject *meta = &PyArrayDTypeMeta_Type;\n"
        "    int ok = f8 != NULL && meta != NULL && npy_creal(z) == 1.0 && libdivide_s64_do(49, &d) == 7;\n"
        "    Py_XDECREF(f8);\n    return ok ? 0 : 7;\n}\n")
random_refs = [ref("doc/source/reference/random/c-api.rst", "npyrandom"),
               ref("doc/source/reference/random/c-api.rst", "in ``numpy/random/lib``")] if (SRC / "doc/source/reference/random/c-api.rst").exists() else []
CXXINC = ["numpy/arrayobject.h", "numpy/ndarrayobject.h", "numpy/ndarraytypes.h", "numpy/arrayscalars.h",
          "numpy/ufuncobject.h", "numpy/random/bitgen.h", "numpy/random/distributions.h"]
CXXHEAD = "".join("#include <%s>\n" % h for h in CXXINC)
CXX1 = ("#define PY_SSIZE_T_CLEAN\n#include <Python.h>\n#define PY_ARRAY_UNIQUE_SYMBOL OPTIMBENCH_NPQ_CXX_API\n"
        "#define PY_UFUNC_UNIQUE_SYMBOL OPTIMBENCH_NPQ_CXX_UFUNC_API\n"
        "#define NPY_NO_DEPRECATED_API NPY_1_21_API_VERSION\n" + CXXHEAD +
        "static uint64_t next64(void *st) { return ++*static_cast<uint64_t *>(st) * 0x9E3779B97F4A7C15ULL; }\n"
        "static uint32_t next32(void *st) { return static_cast<uint32_t>(next64(st) >> 32); }\n"
        "static double nextd(void *st) { return (next64(st) >> 11) * (1.0 / 9007199254740992.0); }\n"
        "int consumer_cxx_two();\n"
        "int main() {\n    uint64_t state = 1;\n    bitgen_t g = {&state, next64, next32, nextd, nullptr};\n"
        "    double vals[4];\n    random_standard_uniform_fill(&g, 4, vals);\n"
        "    for (double v : vals) if (!(v >= 0.0 && v < 1.0)) return 6;\n    Py_Initialize();\n"
        "    if (_import_array() < 0) { PyErr_Print(); return 2; }\n"
        "    if (_import_umath() < 0) { PyErr_Print(); return 3; }\n    return consumer_cxx_two();\n}\n")
CXX2 = ("#define PY_SSIZE_T_CLEAN\n#include <Python.h>\n#define NO_IMPORT_ARRAY\n#define NO_IMPORT_UFUNC\n"
        "#define PY_ARRAY_UNIQUE_SYMBOL OPTIMBENCH_NPQ_CXX_API\n#define PY_UFUNC_UNIQUE_SYMBOL OPTIMBENCH_NPQ_CXX_UFUNC_API\n"
        "#define NPY_NO_DEPRECATED_API NPY_1_21_API_VERSION\n" + CXXHEAD +
        "int consumer_cxx_two() {\n    npy_intp dims[1] = {5};\n"
        "    PyObject *a = PyArray_ZEROS(1, dims, NPY_DOUBLE, 0);\n"
        "    int ok = a != nullptr && PyArray_SIZE(reinterpret_cast<PyArrayObject *>(a)) == 5;\n"
        "    Py_XDECREF(a);\n    return ok ? 0 : 7;\n}\n")
cxx_default_refs = [
    ref("numpy/random/_examples/cython/meson.build", "project('random-build-examples', 'c', 'cpp', 'cython')"),
    ref("numpy/random/_examples/cython/meson.build", "override_options : ['cython_language=cpp'],"),
    ref("numpy/random/_examples/cython/meson.build", "'extending_cpp',"),
    ref("numpy/__init__.cython-30.pxd", 'cdef extern from "numpy/arrayobject.h":'),
    ref("numpy/__init__.cython-30.pxd", 'cdef extern from "numpy/ufuncobject.h":'),
    ref("numpy/random/__init__.pxd", 'cdef extern from "numpy/random/bitgen.h":'),
    ref("numpy/random/c_distributions.pxd", 'cdef extern from "numpy/random/distributions.h":'),
]


def ref_build(path, needle):
    raw = (BUILD / path).read_bytes()
    text = raw.decode()
    line = next(l for l in text.splitlines() if needle in l)
    return {"path": f"pristine-build/{path}", "sha256": hashlib.sha256(raw).hexdigest(), "quote": line.strip()}


IB = "site/numpy/_core/include/numpy/"
CXXX = ["numpy/npy_no_deprecated_api.h", "numpy/npy_os.h", "numpy/arrayobject.h", "numpy/npy_math.h",
        "numpy/halffloat.h", "numpy/npy_3kcompat.h", "numpy/npy_2_complexcompat.h", "numpy/random/libdivide.h"]
CXXXHEAD = "".join("#include <%s>\n" % h for h in CXXX)
CXXX1 = ("#define PY_SSIZE_T_CLEAN\n#include <Python.h>\n" + CXXXHEAD.replace(
             "#include <numpy/npy_os.h>\n", "#include <numpy/npy_os.h>\n#define PY_ARRAY_UNIQUE_SYMBOL OPTIMBENCH_NPQ_CXXX_API\n", 1) +
         "int consumer_cxxx_two();\n"
         "int main() {\n    Py_Initialize();\n    if (_import_array() < 0) { PyErr_Print(); return 2; }\n"
         "    double h = npy_half_to_double(npy_double_to_half(2.5));\n"
         "#ifndef NPY_OS_LINUX\n    return 9;\n#endif\n"
         "    return h == 2.5 && npy_isfinite(npy_nextafter(1.0, 2.0)) ? consumer_cxxx_two() : 4;\n}\n")
CXXX2 = ("#define PY_SSIZE_T_CLEAN\n#include <Python.h>\n" + CXXXHEAD.replace(
             "#include <numpy/npy_os.h>\n", "#include <numpy/npy_os.h>\n#define NO_IMPORT_ARRAY\n#define PY_ARRAY_UNIQUE_SYMBOL OPTIMBENCH_NPQ_CXXX_API\n", 1) +
         "int consumer_cxxx_two() {\n    npy_cdouble z;\n    NPY_CSETREAL(&z, 3.0); NPY_CSETIMAG(&z, 4.0);\n"
         "    libdivide::libdivide_s64_t d = libdivide::libdivide_s64_gen(9);\n"
         "    PyTypeObject *meta = &PyArrayDTypeMeta_Type;\n"
         "    PyArray_Descr *f8 = PyArray_DescrFromType(NPY_FLOAT64);\n"
         "    int ok = meta != nullptr && f8 != nullptr && npy_cabs(z) == 5.0 && libdivide::libdivide_s64_do(81, &d) == 9;\n"
         "    Py_XDECREF(f8);\n    return ok ? 0 : 7;\n}\n")
cxxx_refs = cxx_default_refs + [
    ref("numpy/_core/include/numpy/npy_math.h", "#ifdef __cplusplus"),
    ref("numpy/_core/include/numpy/halffloat.h", "#ifdef __cplusplus"),
    ref("numpy/_core/include/numpy/npy_3kcompat.h", "#ifdef __cplusplus"),
    ref("numpy/_core/include/numpy/ndarraytypes.h", "#ifdef __cplusplus"),
    ref("numpy/_core/include/numpy/ndarraytypes.h", '#include "dtype_api.h"'),
    ref("numpy/_core/include/numpy/ndarrayobject.h", "#ifdef __cplusplus"),
    ref("numpy/_core/include/numpy/ndarrayobject.h", '#include "npy_2_compat.h"'),
    ref_build(IB + "__multiarray_api.h", '#include "numpy/_public_dtype_api_table.h"'),
    ref("numpy/_core/include/numpy/npy_2_complexcompat.h", "#include <numpy/npy_math.h>"),
    ref_build(IB + "random/libdivide.h", "#if defined(__cplusplus)"),
    ref("numpy/_core/include/numpy/npy_no_deprecated_api.h", "must be first among numpy includes"),
    ref("numpy/_core/include/numpy/npy_os.h", "#define NPY_OS_LINUX"),
]
CXXX_HEADERS = ["numpy/npy_no_deprecated_api.h", "numpy/npy_os.h", "numpy/npy_math.h", "numpy/halffloat.h",
                "numpy/npy_3kcompat.h", "numpy/npy_2_complexcompat.h", "numpy/random/libdivide.h",
                "numpy/dtype_api.h", "numpy/npy_2_compat.h", "numpy/_public_dtype_api_table.h"]
cells = [
    {"id": "c-compiler-default-umbrella", "language": "c", "dialect": "compiler-default",
     "support_basis": "pinned-upstream-default-flags", "supported": True,
     "support_refs": c_default_refs,
     "translation_units": [{"name": "consumer_main.c", "source": TU1},
                           {"name": "consumer_two.c", "source": TU2}], **base},
    {"id": "c-compiler-default-extended", "language": "c", "dialect": "compiler-default",
     "support_basis": "pinned-upstream-default-flags", "supported": True,
     "support_refs": c_default_refs + random_refs + [
         ref("numpy/_core/tests/examples/limited_api/meson.build", "npy_target_args = ['-DNPY_TARGET_VERSION=' + npy_macro]")],
     "headers": ["site/numpy/_core/include/" + h for h in XINC + ["numpy/_public_dtype_api_table.h"]],
     "defines": ["NPY_TARGET_VERSION=NPY_2_0_API_VERSION"],
     "features": ["NPY_NO_DEPRECATED_API=NPY_1_21_API_VERSION", "npyrandom static library",
                  "PY_ARRAY_UNIQUE_SYMBOL/NO_IMPORT_ARRAY split"],
     "include_context": {"id": "python-extension-extended", "includes": XINC},
     "include_dirs": ["site/numpy/_core/include"],
     "libraries": ["site/numpy/random/lib/libnpyrandom.a", "site/numpy/_core/lib/libnpymath.a"],
     "compile_flags": PY, "link_flags": LINK, "compiler": "gcc",
     "symbol_refs": ["random_standard_uniform", "PyArray_DescrFromType", "_import_array",
                     "libdivide_s64_gen", "npy_csetreal", "PyArrayDTypeMeta_Type"],
     "translation_units": [{"name": "consumer_x_main.c", "source": XTU1},
                           {"name": "consumer_x_two.c", "source": XTU2}]},
    {"id": "cxx-compiler-default-cython-context", "language": "c++", "dialect": "compiler-default",
     "support_basis": "pinned-upstream-default-flags", "supported": True,
     "support_refs": cxx_default_refs,
     "headers": ["site/numpy/_core/include/" + h for h in CXXINC + [
         "numpy/npy_common.h", "numpy/numpyconfig.h", "numpy/_numpyconfig.h", "numpy/npy_endian.h",
         "numpy/npy_cpu.h", "numpy/utils.h", "numpy/__multiarray_api.h", "numpy/__ufunc_api.h",
         "numpy/_neighborhood_iterator_imp.h"]],
     "features": ["C++ consumer of the headers Cython's numpy/numpy.random pxd extern blocks include",
                  "npyrandom static library", "PY_ARRAY_UNIQUE_SYMBOL/NO_IMPORT_ARRAY split"],
     "include_context": {"id": "cython-cpp-extension-context", "includes": CXXINC},
     "include_dirs": ["site/numpy/_core/include"],
     "libraries": ["site/numpy/random/lib/libnpyrandom.a", "site/numpy/_core/lib/libnpymath.a"],
     "compile_flags": PY, "link_flags": LINK, "compiler": "g++",
     "symbol_refs": ["random_standard_uniform_fill", "_import_array", "_import_umath", "PyArray_ZEROS"],
     "translation_units": [{"name": "consumer_cxx_main.cpp", "source": CXX1},
                           {"name": "consumer_cxx_two.cpp", "source": CXX2}]},
    {"id": "cxx-compiler-default-extended", "language": "c++", "dialect": "compiler-default",
     "support_basis": "pinned-upstream-default-flags", "supported": True,
     "support_refs": cxxx_refs,
     "headers": ["site/numpy/_core/include/" + h for h in CXXX_HEADERS],
     "features": ["global default C++ flags from extending_cpp", "extern-C/compatibility guards or guarded umbrella inclusion per header",
                  "npy_no_deprecated_api.h first among numpy includes", "NPY_TARGET_VERSION=NPY_2_0_API_VERSION"],
     "defines": ["NPY_TARGET_VERSION=NPY_2_0_API_VERSION"],
     "include_context": {"id": "cpp-extension-extended", "includes": CXXX},
     "include_dirs": ["site/numpy/_core/include"],
     "libraries": ["site/numpy/_core/lib/libnpymath.a"],
     "compile_flags": PY, "link_flags": LINK, "compiler": "g++",
     "symbol_refs": ["npy_half_to_double", "npy_nextafter", "libdivide_s64_gen", "PyArrayDTypeMeta_Type",
                     "PyArray_DescrFromType", "npy_cabs", "_import_array"],
     "translation_units": [{"name": "consumer_cxxx_main.cpp", "source": CXXX1},
                           {"name": "consumer_cxxx_two.cpp", "source": CXXX2}]},
]
informational = []
for dialect, language, refs, reason in (
        ("c99", "c", build_std_refs, "No pinned upstream statement that downstream consumers must or may use C99"),
        ("c11", "c", build_std_refs, "meson.build c_std=c11 governs NumPy's own build, not a downstream-consumer contract"),
        ("gnu89", "c", build_std_refs, "No pinned upstream consumer evidence for GNU89; not guessed"),
        ("c++11", "c++", cxx_refs, "Headers carry extern \"C\" guards, but no pinned upstream C++ consumer build/standard found yet"),
        ("c++17", "c++", cxx_refs + build_std_refs, "cpp_std=c++17 governs NumPy's own build; consumer C++ support evidence pending")):
    informational.append({"id": f"{language.replace('+', 'x')}-{dialect.replace('+', 'x')}-umbrella",
                  "language": language, "dialect": dialect, "support_status": "pending",
                  "supported": None, "reason": reason, "support_refs": refs,
                  "headers": ["site/numpy/_core/include/" + h for h in covered]})
manifest = {"schema": "optimbench-header-matrix-v1", "task": "numpy-quantile",
            "public_header_inventory": headers,
            "uncovered_headers": sorted(set(headers) - set(covered) - set(XINC) - {"numpy/_public_dtype_api_table.h"}),
            "informational_unresolved_dialects": informational,
            "cxx_scope": {"status": "established_for_all_installed_headers",
                          "basis": "global compiler-default C++ flags from pinned extending_cpp (cython_language=cpp, no cpp_std override); per-header legal context from pinned extern-C/compatibility guards or guarded umbrella inclusion; two real C++ TUs per cell",
                          "cells": ["cxx-compiler-default-cython-context", "cxx-compiler-default-extended"],
                          "no_numeric_minimum_inferred": True,
                          "unmapped_contexts": []},
            "required_cells": [c["id"] for c in cells], "cells": cells}
Path(sys.argv[1]).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
print(json.dumps({"headers": len(headers), "covered": len(covered), "cells": len(cells)}))
