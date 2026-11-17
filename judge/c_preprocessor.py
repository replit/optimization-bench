"""Read-only, offline C/C++ header preprocessing; also runs inside task images.

Build metadata is data, never a shell script: only preprocessing flags survive.
No library-specific export macros or compiler wrappers are evaluated here.
"""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys


class Unavailable(RuntimeError):
    pass


def public_roots(root, paths):
    root, result = Path(root).resolve(), []
    for relative in paths:
        path = (root / relative).resolve()
        if Path(relative).is_absolute() or not path.is_relative_to(root) or not path.is_dir():
            raise ValueError("Invalid declared public include root")
        result.append(str(path))
    return result


def words(value):
    try:
        return shlex.split(value)
    except ValueError:
        return []


def metadata(root):
    """Recover compiler/CPP context from CMake, compile databases and Makefiles."""
    root = Path(root).resolve()
    records, mappings, compilers, sources = [], [], {}, []
    caches = sorted(root.rglob("CMakeCache.txt"))
    for path in caches:
        entries = dict(re.findall(r"(?m)^([^/#:\n]+):[^=\n]+=(.*)$", path.read_text(errors="replace")))
        old = entries.get("CMAKE_CACHEFILE_DIR")
        if old:
            ancestor = Path(old)
            for _ in path.parent.relative_to(root).parts:
                ancestor = ancestor.parent
            mappings.append((str(ancestor), str(root)))
        for language, key in (("c", "CMAKE_C_COMPILER"), ("cpp", "CMAKE_CXX_COMPILER")):
            if entries.get(key):
                compilers.setdefault(language, entries[key])
        sources.append(str(path.relative_to(root)))
    makefiles = sorted(root.rglob("Makefile")) + sorted(root.rglob("flags.make"))
    for path in makefiles:
        text = path.read_text(errors="replace")
        # Out-of-tree Autotools build paths need the same relocation as CMake.
        old = re.search(r"(?m)^abs_builddir\s*=\s*(/\S+)", text)
        if old:
            ancestor = Path(old[1])
            for _ in path.parent.relative_to(root).parts:
                ancestor = ancestor.parent
            mappings.append((str(ancestor), str(root)))
    mappings.sort(key=lambda pair: len(pair[0]), reverse=True)

    def relocate(value):
        for old, new in mappings:
            value = value.replace(old + "/", new + "/")
            if value == old:
                value = new
        return value

    for path in sorted(root.rglob("compile_commands.json")):
        for row in json.loads(path.read_text()):
            argv = row.get("arguments") or words(row.get("command", ""))
            if not argv:
                continue
            directory = relocate(row.get("directory", str(path.parent)))
            source = relocate(row.get("file", directory))
            records.append((source, directory, [relocate(a) for a in argv[1:]]))
            lang = "cpp" if Path(source).suffix in (".cc", ".cpp", ".cxx") else "c"
            compilers.setdefault(lang, argv[0])
        sources.append(str(path.relative_to(root)))
    for path in makefiles:
        text = path.read_text(errors="replace").replace("\\\n", " ")
        variables = dict(re.findall(r"(?m)^([A-Za-z_]\w*)\s*(?::|\+|\?)?=\s*(.*)$", text))

        def expand(value):
            for _ in range(12):
                updated = re.sub(r"\$\((\w+)\)|\$\{(\w+)\}",
                                 lambda m: variables.get(m[1] or m[2], ""), value)
                if updated == value:
                    break
                value = updated
            return value
        flags = []
        for key in ("DEFS", "DEFAULT_INCLUDES", "INCLUDES", "AM_CPPFLAGS", "CPPFLAGS",
                    "C_DEFINES", "C_INCLUDES", "C_FLAGS", "CXX_DEFINES", "CXX_INCLUDES",
                    "CXX_FLAGS", "CFLAGS", "CXXFLAGS"):
            flags += words(relocate(expand(variables.get(key, ""))))
        if flags:
            records.append((str(path.parent), str(path.parent), flags))
            sources.append(str(path.relative_to(root)))
        for lang, key in (("c", "CC"), ("cpp", "CXX")):
            compiler = words(expand(variables.get(key, "")))
            if compiler and "$" not in compiler[0]:
                compilers.setdefault(lang, compiler[0])
    for path in sorted(root.rglob("build.ninja")):
        text = path.read_text(errors="replace")
        current, flags = str(path.parent), []
        for line in [*text.splitlines(), "build "]:
            if line.startswith("build "):
                if flags:
                    records.append((current, str(path.parent), flags))
                flags = []
                tokens = words(line)
                # The input after the rule identifies the source context.
                if len(tokens) >= 4:
                    current = relocate(tokens[3])
            elif re.match(r"\s+(?:DEFINES|INCLUDES|FLAGS)\s*=", line):
                flags += words(relocate(line.split("=", 1)[1]))
        sources.append(str(path.relative_to(root)))
    return records, compilers, sorted(set(sources)), relocate


def cpp_flags(tokens, directory, language):
    """Keep only arguments that affect preprocessing, never plugins/output/actions."""
    flags, index = [], 0
    paired = {"-I", "-D", "-U", "-isystem", "-iquote", "-idirafter", "-include",
              "-imacros", "--sysroot", "-isysroot"}
    paths = paired - {"-D", "-U"}
    while index < len(tokens):
        token = tokens[index]
        if token in paired and index + 1 < len(tokens):
            value = tokens[index + 1]
            if token in paths and not Path(value).is_absolute():
                value = str(Path(directory) / value)
            flags += [token, value]
            index += 2
            continue
        if token.startswith(("-D", "-U", "-I")) and len(token) > 2:
            value = token[2:]
            if token[:2] == "-I" and not Path(value).is_absolute():
                value = str(Path(directory) / value)
            flags.append(token[:2] + value)
        elif token.startswith("-std="):
            if ("++" in token) == (language == "cpp"):
                flags.append(token)
        elif token in ("-pthread", "-undef", "-nostdinc", "-nostdinc++"):
            flags.append(token)
        index += 1
    return flags


def missing_external_include(error, root, include_roots):
    """Recognize only absent external angle-includes, not invalid task headers.

    A quoted include is conservatively task-owned. An angle include whose
    namespace exists in a task include root is task-owned too. Missing task
    files, #error and other compiler errors never authorize raw extraction.
    """
    failures = [line for line in error.splitlines() if re.search(r"\b(?:fatal )?error:", line)]
    if not failures:
        return None
    missing = []
    for line in failures:
        match = re.match(r"(.+?):(\d+):(?:\d+:)?\s*fatal error:\s*(.+)", line)
        if not match:
            return None
        diagnostic = match[3]
        name = re.match(r"(.+?): No such file or directory\s*$", diagnostic)
        if name:
            name = name[1].strip("'\"")
        else:
            name = re.match(r"'([^']+)' file not found\s*$", diagnostic)
            if not name:
                return None
            name = name[1]
        owner = Path(match[1])
        if not owner.is_file():
            return None
        source = owner.read_text(errors="replace").splitlines()
        index = int(match[2]) - 1
        if index >= len(source) or not re.match(r"\s*#\s*include\s*<[^>]+>", source[index]):
            return None
        wanted = Path(name)
        if wanted.is_absolute():
            if wanted.is_relative_to(root):
                return None
        else:
            # A known task include namespace (even with the final file absent)
            # must not be reclassified as an unavailable third-party SDK.
            for directory in include_roots:
                if (directory / wanted).exists():
                    return None
                if len(wanted.parts) > 1 and (directory / wanted.parts[0]).is_dir():
                    return None
        missing.append(name)
    return sorted(set(missing))


def preprocess(root, config):
    root = Path(root).resolve()
    files = sorted({p.resolve() for pattern in config["files"]
                    for p in root.glob(pattern) if p.is_file()})
    if not files:
        raise ValueError("No configured public C/C++ headers")
    if any(not p.is_relative_to(root) for p in files):
        raise ValueError("Public header escapes build")
    records, compilers, sources, relocate = metadata(root)
    explicit = config.get("preprocess", {})
    groups = {}
    for path in files:
        lang = "cpp" if config["language"] == "cpp" or path.suffix in (".hpp", ".hh", ".hxx") else "c"
        groups.setdefault(lang, []).append(path)
    units, commands, header_modes = [], [], {}
    expected = config.get("_pristine_header_modes")
    for lang, headers in groups.items():
        compiler = explicit.get("compiler") or compilers.get(lang) or ("c++" if lang == "cpp" else "cc")
        # Do not execute a compiler command supplied as a shell expression, or
        # any executable from the candidate's editable build directory.
        if (not re.search(r"(?:^|[-/])(?:cc|gcc|clang|c\+\+|g\+\+|clang\+\+)(?:-[\d.]+)?$", compiler)
                or (Path(compiler).is_absolute() and Path(compiler).is_relative_to(root))):
            raise ValueError("Unsupported compiler executable in build metadata")
        executable = shutil.which(compiler)
        if not executable:
            raise Unavailable("Compiler unavailable: " + compiler)

        def score(record):
            source = Path(record[0])
            if not source.is_absolute():
                source = Path(record[1]) / source
            return max(len(os.path.commonpath((str(h.parent), str(source.parent))).split("/"))
                       for h in headers)
        chosen = max(records, key=score) if records else ("", str(root), [])
        flags = cpp_flags(chosen[2], chosen[1], lang)
        for directory in explicit.get("include_dirs", []):
            flags += ["-I", str(root / directory) if not Path(directory).is_absolute() else directory]
        flags += ["-D" + value for value in explicit.get("defines", [])]
        flags += cpp_flags(explicit.get("flags", []), str(root), lang)
        # Public sibling includes and installed <library/header.h> layouts are
        # generic fallbacks; actual build-recorded include ordering comes first.
        includes = {root}
        for header in headers:
            parent = header.parent
            while parent.is_relative_to(root):
                includes.add(parent)
                if parent == root:
                    break
                parent = parent.parent
        for directory in sorted(includes):
            flags += ["-I", str(directory)]
        argv = [executable, "-E", "-x", "c++" if lang == "cpp" else "c", *flags, "-"]
        # Include umbrella/shallower headers first. Do not directly re-include a
        # subordinate header already visited by the compiler: some public APIs
        # intentionally reject that, even after their umbrella was included.
        seen = set()
        for header in sorted(headers, key=lambda p: (len(p.parts), str(p))):
            relative = str(header.relative_to(root))
            if str(header) in seen:
                header_modes[relative] = {"mode": "preprocessed"}
                continue
            text = '#include ' + json.dumps(str(header)) + "\n"
            result = subprocess.run(argv, input=text, text=True, capture_output=True,
                                    cwd=root, timeout=90)
            if result.returncode:
                internal_roots = set(includes)
                for i, token in enumerate(flags):
                    if token == "-I" and i + 1 < len(flags):
                        value = flags[i + 1]
                    elif token.startswith("-I") and len(token) > 2:
                        value = token[2:]
                    else:
                        continue
                    path = Path(value)
                    if path.is_relative_to(root):
                        internal_roots.add(path)
                missing = missing_external_include(result.stderr, root, internal_roots)
                if not missing:
                    raise ValueError("Public header preprocessing failed: " + result.stderr[-4000:])
                detail = {"mode": "raw-unpreprocessable", "missing_external_includes": missing,
                          "error": result.stderr}
                if expected is not None and (
                        expected.get(relative, {}).get("mode") != detail["mode"] or
                        expected[relative].get("missing_external_includes") != missing):
                    raise ValueError("Candidate-only external include failure: " + relative + "\n" + result.stderr)
                header_modes[relative] = detail
                continue
            header_modes[relative] = {"mode": "preprocessed"}
            units.append({"language": lang, "text": result.stdout})
            for marker in re.finditer(r'(?m)^\s*#\s+\d+\s+("(?:[^"\\]|\\.)*")', result.stdout):
                seen.add(json.loads(marker[1]))
        commands.append({"compiler": compiler, "language": lang, "flags": flags})
    if expected is not None:
        for header, old in expected.items():
            if header_modes.get(header, {}).get("mode") != old["mode"]:
                raise ValueError("Candidate public-header extraction mode changed: " + header)
    raw = [name for name, detail in header_modes.items() if detail["mode"] == "raw-unpreprocessable"]
    mode = "preprocessed" if not raw else "preprocessed+raw-unpreprocessable" if units else "raw-unpreprocessable"
    return {"units": units, "headers": [str(p) for p in files],
            "public_include_roots": public_roots(root, config.get("public_include_roots", [])),
            "raw_headers": raw,
            "raw_header_paths": [str(root / name) for name in raw],
            "diagnostics": {"mode": mode, "commands": commands, "build_metadata": sources,
                            "headers": dict(sorted(header_modes.items()))}}


if __name__ == "__main__":
    config = json.loads(Path(sys.argv[1]).read_text())
    try:
        result = preprocess("/candidate", config)
    except Unavailable as error:
        result = {"unavailable": str(error)}
    Path("/scratch/preprocessed.json").write_text(json.dumps(result))
