"""Review added process-global configuration writes; never inspect unchanged lines."""
import ast
import difflib
import re
import json
import hashlib
from pathlib import Path

FENV_WRITES = r"\b(?:fesetround|fesetenv|feupdateenv|feholdexcept|feclearexcept|feraiseexcept|fesetexceptflag|_MM_SET_ROUNDING_MODE|_MM_SET_FLUSH_ZERO_MODE|_MM_SET_DENORMALS_ZERO_MODE|_mm_setcsr|_controlfp|_controlfp_s|_control87)\s*\("

PATTERNS = {
    ".java": [
        r"\bSystem\.(?:setProperty|clearProperty|setProperties)\s*\(",
        r"\b(?:Locale|TimeZone)\.setDefault\s*\(",
        r"\bThread\.setDefaultUncaughtExceptionHandler\s*\("],
    ".go": [
        r"\bdebug\.(?:SetGCPercent|SetMemoryLimit|SetMaxStack)\s*\(",
        r"\bos\.(?:Setenv|Unsetenv|Clearenv)\s*\(", r"\bruntime\.MemProfileRate\s*="],
    ".py": [
        r"\bgc\.(?:disable|enable|set_threshold|freeze|unfreeze)\s*\(",
        r"\bsys\.(?:setswitchinterval|setrecursionlimit|set_int_max_str_digits)\s*\(",
        r"\bos\.(?:putenv|unsetenv)\s*\(",
        r"\bos\.environ(?:\s*\[[^\]\n]*\]\s*(?:[|+]?=(?!=))|\.(?:update|clear|pop|popitem|setdefault|__setitem__|__delitem__)\s*\()",
        r"\bos\.environ\s*(?:[|+]?=(?!=))",
        r"\bdel\s+os\.environ\b",
        r"\b(?:signal\.signal|resource\.setrlimit)\s*\("],
    ".js": [
        r"\bv8\.setFlagsFromString\s*\(",
        r"\bprocess\.env(?:\s*\[[^\]\n]*\]|\.\w+)\s*=(?!=)",
        r"\bprocess\.env\s*=(?!=)",
        r"\b(?:delete\s+process\.env|Object\.assign\s*\(\s*process\.env)",
        r"\bprocess\.(?:setSourceMapsEnabled|setUncaughtExceptionCaptureCallback|on|once)\s*\("],
    ".c": [
        FENV_WRITES,
        r"\b(?:setenv|unsetenv|putenv|clearenv|mallopt|malloc_trim|setrlimit|prlimit|signal|sigaction)\s*\(",
        r"\benviron\s*=", r"\b(?:__malloc_hook|__free_hook)\s*=",
        r"\b(?:malloc|calloc|realloc|free)\s*\([^;{}]*\)\s*\{",
        r"\boperator\s+(?:new|delete)\s*(?:\[\])?\s*\([^;{}]*\)\s*(?:noexcept\s*)?\{"],
    ".rs": [
        FENV_WRITES,
        r"\b(?:std::)?env::(?:set_var|remove_var)\s*\(",
        r"#\s*\[\s*global_allocator\s*\]", r"\bimpl\s+(?:\w+::)*GlobalAlloc\b",
        r"\b(?:libc::)?(?:setenv|unsetenv|putenv|mallopt|setrlimit|signal|sigaction)\s*\("],
}


def code_only(text, suffix):
    from .lexical import code_only as mask
    return mask(text, suffix)


def floating_assembly(text, suffix):
    """Inspect only grammar-recognized assembly strings / LLVM link attributes.

    Ordinary literals remain masked. Assembly is executable code even though its
    template is a string; do not unmask unrelated strings or comments.
    """
    from .lexical import parse
    if suffix not in (".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".rs"):
        return []
    tree, data = parse(text, suffix)
    found, pending = [], [tree.root_node]
    instruction = re.compile(r"(?:^|\\[nt]|[\n;])\s*(v?ldmxcsr|fldcw|fldenv|frstor|fxrstor|xrstor)\b", re.I)
    while pending:
        node = pending.pop()
        raw = data[node.start_byte:node.end_byte].decode()
        if suffix == ".rs" and node.type == "attribute_item":
            attribute = next((child for child in node.named_children if child.type == "attribute"), None)
            parts = attribute.named_children if attribute else []
            if (parts and data[parts[0].start_byte:parts[0].end_byte] == b"link_name" and
                    any(child.type == "string_literal" and re.fullmatch(
                        r'"llvm\.(?:x86\.)?(?:sse\.)?(?:ldmxcsr|fldcw)"',
                        data[child.start_byte:child.end_byte].decode()) for child in parts[1:])):
                found.append((len(data[:node.start_byte].decode()), len(data[:node.end_byte].decode()),
                              "floating-point LLVM control intrinsic"))
        assembly = node.type == "gnu_asm_expression"
        if suffix == ".rs" and node.type == "macro_invocation":
            macro = node.child_by_field_name("macro")
            assembly = macro is not None and data[macro.start_byte:macro.end_byte].decode().split("::")[-1] in (
                "asm", "global_asm", "llvm_asm")
        if assembly:
            children = list(node.children)
            while children:
                child = children.pop()
                if child.type in ("string_literal", "raw_string_literal"):
                    literal = data[child.start_byte:child.end_byte].decode()
                    start = literal.find('"') + 1
                    for match in instruction.finditer(literal[start:literal.rfind('"')]):
                        first = len(data[:child.start_byte].decode()) + start + match.start(1)
                        found.append((first, first + len(match[1]), "floating-point assembly control write"))
                else:
                    children.extend(child.children)
            continue
        pending.extend(node.children)
    return found


def findings(original, candidate):
    result = []
    for name, text in sorted(candidate.items()):
        if text == original.get(name):
            continue
        suffix = "." + name.rsplit(".", 1)[-1]
        lexical_suffix = suffix
        suffix = {".cpp": ".c", ".cc": ".c", ".cxx": ".c", ".h": ".c", ".hpp": ".c",
                  ".hh": ".c", ".hxx": ".c", ".mjs": ".js", ".cjs": ".js",
                  ".jsx": ".js", ".ts": ".js", ".tsx": ".js"}.get(suffix, suffix)
        patterns = list(PATTERNS.get(suffix, []))
        aliases = {}
        if not patterns:
            continue
        # Recognize common import aliases without flagging imports themselves.
        if suffix == ".py":
            try:
                for node in ast.walk(ast.parse(text)):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.asname:
                                aliases[alias.asname] = alias.name
                                patterns += [p.replace(re.escape(alias.name) + r"\.",
                                                       re.escape(alias.asname) + r"\.")
                                             for p in PATTERNS[suffix] if re.escape(alias.name) + r"\." in p]
                    elif isinstance(node, ast.ImportFrom) and node.module in ("gc", "sys", "os", "signal"):
                        for alias in node.names:
                            qualified = node.module + "." + alias.name
                            aliases[alias.asname or alias.name] = qualified
                            if any(re.search(p, qualified + "(") for p in PATTERNS[suffix]):
                                patterns.append(r"\b" + re.escape(alias.asname or alias.name) + r"\s*\(")
            except SyntaxError:
                pass
        if suffix == ".go":
            for alias, package in re.findall(r'(\w+)\s+"(runtime/debug|runtime|os)"', text):
                canonical = {"runtime/debug": "debug", "runtime": "runtime", "os": "os"}[package]
                aliases[alias] = canonical
                patterns += [p.replace(r"\b" + canonical + r"\.", r"\b" + alias + r"\.")
                             for p in PATTERNS[suffix] if r"\b" + canonical + r"\." in p]
        old, new = original.get(name, "").splitlines(), text.splitlines()
        added = set()
        for tag, _, _, start, end in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
            if tag in ("insert", "replace"):
                added.update(range(start + 1, end + 1))
        masked = code_only(text, lexical_suffix)
        matches = [(match.start(), match.end(), pattern) for pattern in patterns
                   for match in re.finditer(pattern, masked)]
        matches += floating_assembly(text, lexical_suffix)
        for start, end, pattern in matches:
            line = masked.count("\n", 0, start) + 1
            last = masked.count("\n", 0, end) + 1
            if any(i in added for i in range(line, last + 1)):
                row = {"path": name, "line": line, "added_lines": [
                    {"line": i, "text": new[i - 1]} for i in range(line, last + 1) if i in added],
                    "pattern": pattern, "reason": "process-global runtime configuration"}
                operation = masked[start:end].split("(", 1)[0].strip()
                if not operation:
                    operation = text[start:end].strip()  # Explicit grammar-recognized assembly only.
                for alias, canonical in aliases.items():
                    if operation == alias or operation.startswith(alias + "."):
                        operation = canonical + operation[len(alias):]
                        break
                row["operation"] = operation
                row["category"] = category(operation, suffix, text, start, lexical_suffix)
                row.update(source_sha256=hashlib.sha256(text.encode()).hexdigest(),
                           start_byte=len(text[:start].encode()), end_byte=len(text[:end].encode()))
                if not any(r["path"] == name and r["line"] == line and r["operation"] == operation for r in result):
                    result.append(row)
    return result


def category(operation, suffix, text="", start=0, lexical_suffix=None):
    """Configuration semantics, not a package or optimization-target allowlist."""
    name = operation.split("::")[-1]
    if (any(value in operation for value in ("global_allocator", "GlobalAlloc", "__malloc_hook", "__free_hook"))
            or re.search(r"\boperator\s+(new|delete)\b", operation)
            or name in ("malloc", "calloc", "realloc", "free", "mallopt", "malloc_trim")):
        return "allocator"
    if name in ("setrlimit", "prlimit") or operation == "resource.setrlimit":
        return "resource_limits"
    if name in ("signal", "sigaction"):
        return "native_signal_handlers"
    if operation == "signal.signal":
        return "python_signal_handlers"
    if operation == "v8.setFlagsFromString":
        return "vm_flags"
    if operation in ("gc.enable", "gc.disable"):
        return "gc_enabled"
    if operation == "gc.set_threshold":
        return "gc_thresholds"
    if operation in ("gc.freeze", "gc.unfreeze"):
        return "gc_frozen"
    if operation == "sys.setrecursionlimit":
        return "recursion_limit"
    if operation == "sys.setswitchinterval":
        return "switch_interval"
    if operation.startswith("os.environ") or operation.startswith("del os.environ") or operation.startswith("process.env") or operation.startswith("delete process.env") or operation.startswith("Object.assign"):
        return "environment_mapping"
    if operation in ("os.Setenv", "os.Unsetenv", "os.Clearenv"):
        return "environment_mapping"
    if (operation in ("os.putenv", "os.unsetenv") or name in
            ("setenv", "unsetenv", "putenv", "clearenv", "set_var", "remove_var") or operation.startswith("environ")):
        return "native_environment"
    if operation == "debug.SetGCPercent":
        return "go_gc_percent"
    if name == "fesetround":
        return "fp_rounding"
    if name in ("_MM_SET_ROUNDING_MODE", "_MM_SET_FLUSH_ZERO_MODE", "_MM_SET_DENORMALS_ZERO_MODE"):
        return "fp_mxcsr"
    if operation in ("process.on", "process.once"):
        # The earlier heuristic treated exit hooks as runtime tuning. Inspect a
        # literal event through the grammar; dynamic/signal handlers stay blocking.
        from .lexical import parse
        tree, data = parse(text, lexical_suffix or ".js")
        offset = len(text[:start].encode())
        pending = [tree.root_node]
        while pending:
            node = pending.pop()
            if node.type == "call_expression" and node.start_byte == offset:
                args = node.child_by_field_name("arguments")
                first = next(iter(args.named_children), None) if args else None
                if first is not None and first.type == "string":
                    try:
                        event = ast.literal_eval(data[first.start_byte:first.end_byte].decode())
                    except (SyntaxError, ValueError):
                        break
                    return "lifecycle_hook" if event in ("exit", "beforeExit") else "native_signal_handlers" if event.startswith("SIG") else "unobserved_runtime_setting"
                break
            pending.extend(child for child in node.named_children if child.start_byte <= offset < child.end_byte)
        return "unobserved_runtime_setting"
    return "unobserved_runtime_setting"


def probe_coverage(kind, state):
    """Only capabilities present in BOTH actual snapshots can discharge review."""
    left, right = state.get("baseline", {}), state.get("candidate", {})
    coverage = set()
    if kind == "native":
        rows = [*left.get("native_processes", []), *right.get("native_processes", [])]
        if not left.get("native_processes") or not right.get("native_processes"):
            return coverage
        if all("environment" in row for row in rows):
            coverage.add("native_environment")
            coverage.add("environment_mapping")
        if all(row.get("fp_rounding_mode") is not None for row in rows):
            coverage.add("fp_rounding")
        if all(row.get("mxcsr_control") is not None for row in rows):
            coverage.add("fp_mxcsr")
        return coverage
    keys = {
        "python": {"gc_enabled": "gc_enabled", "gc_thresholds": "gc_thresholds",
                   "gc_frozen": "gc_frozen", "recursion_limit": "recursion_limit",
                   "switch_interval": "switch_interval", "environment_mapping": "environment",
                   "python_signal_handlers": "signal_handlers"},
        "node": {"environment_mapping": "environment", "native_environment": "environment"},
        "goldmark": {"go_gc_percent": "gc_percent", "environment_mapping": "environment"},
        "go_sqlfmt": {"go_gc_percent": "gc_percent", "environment_mapping": "environment"},
    }.get(kind, {})
    for capability, key in keys.items():
        if key in left and key in right:
            coverage.add(capability)
    return coverage


def assess(rows, state, kind):
    """Clean observed state makes heuristics advisory, never unobserved writes."""
    clean = (state.get("passed") is True and state.get("status") == "passed"
             and state.get("differences") == {}
             and isinstance(state.get("baseline"), dict) and bool(state["baseline"])
             and state["baseline"] == state.get("candidate")
             and all(state.get(side, {}).get("status") != "not_configured" for side in ("baseline", "candidate")))
    coverage = probe_coverage(kind, state) if clean else set()
    advisory, blocking = [], []
    from .execution_coverage import candidate_executed
    for row in rows:
        value = dict(row)
        lifecycle = (clean and kind == "node" and row.get("category") == "lifecycle_hook"
                     and all("active_handles" in state.get(side, {}) for side in ("baseline", "candidate")))
        observable = row.get("category") in coverage or lifecycle
        exercised = candidate_executed(row, state, kind)
        if clean and observable and exercised:
            value["review_disposition"] = ("advisory_lifecycle_not_configuration_clean_probe" if lifecycle
                                            else "advisory_clean_observed_probe")
            advisory.append(value)
        else:
            if clean and observable and not exercised:
                value["review_reason"] = "not exercised by representative probe"
            blocking.append(value)
    return {"runtime_configuration_review": blocking, "runtime_configuration_advisory": advisory,
            "runtime_configuration_probe_coverage": {"kind": kind, "clean": clean, "categories": sorted(coverage)}}


def review(task, rows, behavioral):
    settings = json.loads((Path(task) / "tests/judge.json").read_text()).get("process_state", {})
    state = behavioral.get("process_state", {}) if settings.get("enabled") else {}
    return assess(rows or [], state, settings.get("kind") if settings.get("enabled") else None)
