"""Pristine-relative public interface gates; additions are allowed, removals are not."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import ast
import bisect

HERE = Path(__file__).resolve().parent
PROJECTION_VERSION = "externally-visible-projection-v3"


class InterfaceBroken(ValueError):
    def __init__(self, report):
        super().__init__("Public interface was removed or incompatibly changed")
        self.report = report


def contained(root, relative):
    path = (Path(root) / relative).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError("Interface path escapes build")
    return path


def symbols(path, dynamic):
    flags = ["-D"] if dynamic else ["-g"]
    result = subprocess.run(["nm", *flags, "--defined-only", str(path)],
                            capture_output=True, text=True, check=True)
    values = {}
    for line in result.stdout.splitlines():
        match = re.match(r"^\s*(?:[0-9a-fA-F]+)?\s+([A-Za-z])\s+(\S+)\s*$", line)
        if match and match[1].isupper():
            values[match[2]] = match[1]
    if not values:
        raise ValueError("No exported interface symbols: " + str(path))
    return values


def declaration_tokens(node, data, language, type_aliases=None, name_override=None):
    """Ignore local argument bindings, never their types, order or defaults."""
    from .lexical import signature_text

    def declarator_name(current):
        if current is None:
            return None
        if current.type == "identifier":
            return current
        child = current.child_by_field_name("declarator")
        if child is not None:
            return declarator_name(child)
        if current.type == "parenthesized_declarator":
            for child in current.named_children:
                found = declarator_name(child)
                if found is not None:
                    return found
        return None

    def canonical(current, omit=None):
        if current == omit or current.type == "comment" or current.type.endswith("_comment"):
            return ""
        if name_override is not None and current == node.child_by_field_name("name"):
            return name_override
        if language in ("rust", "go") and current.type in ("type_identifier", "lifetime"):
            text = data[current.start_byte:current.end_byte].decode()
            if type_aliases and text in type_aliases:
                return type_aliases[text]
        if language == "go" and current.type == "qualified_type":
            package = current.child_by_field_name("package")
            name = current.child_by_field_name("name")
            binding = data[package.start_byte:package.end_byte].decode()
            path = (type_aliases or {}).get("package:" + binding, binding)
            return path + " . " + data[name.start_byte:name.end_byte].decode()
        if language == "go" and current.type == "parameter_list":
            arguments = []
            for parameter in current.named_children:
                if parameter.type.endswith("_comment") or parameter.type == "comment":
                    continue
                names = parameter.children_by_field_name("name")
                argument_type = parameter.child_by_field_name("type")
                if argument_type is None:
                    raise ValueError("Go parameter has no type")
                value = ("... " if parameter.type == "variadic_parameter_declaration" else "") + canonical(argument_type)
                arguments.extend([value] * max(1, len(names)))
            return "( " + " , ".join(arguments) + " )"
        if language == "rust" and current.type == "parameter":
            # `mut value`, `_` and destructuring are body-local bindings.
            argument_type = current.child_by_field_name("type")
            if argument_type is not None:
                pattern = current.child_by_field_name("pattern")
                receiver = "self : " if pattern is not None and pattern.type == "self" else ""
                return receiver + canonical(argument_type)
        if language in ("c", "cpp") and current.type in (
                "parameter_declaration", "optional_parameter_declaration"):
            omit = declarator_name(current.child_by_field_name("declarator"))
        if language == "java" and current.type == "formal_parameter":
            omit = current.child_by_field_name("name")
        if language == "java" and current.type == "variable_declarator" and current.parent.type == "spread_parameter":
            omit = current.child_by_field_name("name")
        if not current.children or current.type in (
                "string_literal", "raw_string_literal", "char_literal",
                "character_literal", "interpreted_string_literal", "rune_literal"):
            return signature_text(current, data)
        body = (current.child_by_field_name("body") if current.type in (
            "function_definition", "function_item", "function_declaration", "method_declaration") else None)
        parts = []
        result = current.child_by_field_name("result") if language == "go" else None
        for child in current.children:
            if child == body:
                continue
            value = canonical(child, omit)
            if child == result and child.type != "parameter_list":
                value = "( " + value + " )"
            if value:
                parts.append(value)
        return " ".join(parts)

    return canonical(node)


def c_header_declarations(result, allow_empty=False, public_tokens=None, namespaces=None):
    """Grammar-backed public declarations, including project public include roots."""
    from .lexical import parse, signature_text
    allowed = {str(Path(p).resolve()) for p in result["headers"]}
    allowed -= set(result.get("raw_header_paths", []))
    # Public roots are declared metadata, not guessed from an "include" folder:
    # such folders can also contain explicitly non-public/detail headers.
    include_roots = {str(Path(path).resolve()).rstrip("/") + "/"
                     for path in result.get("public_include_roots", [])}
    values = {}
    for unit in result["units"]:
        lines, origins, current = [], [], None
        for line in unit["text"].splitlines(keepends=True):
            marker = re.match(r'^\s*#\s+(?:line\s+)?\d+\s+("(?:[^"\\]|\\.)*")', line)
            if marker:
                current = json.loads(marker[1])
                lines.append("\n")
                origins.append(None)
            else:
                lines.append(line)
                source = str(Path(current).resolve()) if current and not current.startswith("<") else None
                origins.append(source)
                if source and any(source.startswith(prefix) for prefix in include_roots):
                    allowed.add(source)
                if public_tokens is not None and source in allowed:
                    public_tokens.update(re.findall(r"\b[A-Za-z_]\w*\b", line))
        text = "".join(lines)
        tree, data = parse(text, ".cpp" if unit["language"] == "cpp" else ".c")
        starts = [0]
        for line in lines:
            starts.append(starts[-1] + len(line.encode()))

        def origin(node):
            index = bisect.bisect_right(starts, node.start_byte) - 1
            return origins[index] if index < len(origins) else None

        def canonical(node):
            # Inline implementation changes are not declaration changes.
            if node.type == "function_definition":
                return declaration_tokens(node, data, unit["language"]) + " ;"
            return declaration_tokens(node, data, unit["language"])

        boundaries = {"declaration", "type_definition", "function_definition",
                      "struct_specifier", "union_specifier", "enum_specifier",
                      "class_specifier", "template_declaration", "alias_declaration",
                      "using_declaration"}
        def visit(node, namespace=()):
            if node.type == "namespace_definition":
                name = node.child_by_field_name("name")
                namespace += (signature_text(name, data) if name else "<anonymous>",)
                if namespaces is not None and origin(node) in allowed and name:
                    namespaces.update(re.findall(r"[A-Za-z_]\w*", signature_text(name, data)))
            if node.type in boundaries and origin(node) in allowed:
                signature = canonical(node).strip()
                if signature:
                    values["::".join(namespace) + ":" + signature] = True
                return
            # Never collect a local declaration from an inline implementation.
            if node.type == "function_definition":
                return
            for child in node.named_children:
                visit(child, namespace)
        visit(tree.root_node)
    if not values and not allow_empty:
        raise ValueError("No public declarations in preprocessed headers")
    return values


def docker_c_preprocess(root, config, image, cpuset):
    with tempfile.TemporaryDirectory(prefix="interface-cpp-") as temporary:
        scratch = Path(temporary)
        scratch.chmod(0o777)
        (scratch / "config.json").write_text(json.dumps(config))
        command = ["docker", "run", "--rm", "--network", "none", "--user", "10000:10000",
                   "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                   "--pids-limit=512", "--memory=4g", "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m",
                   "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "HOME=/tmp",
                   "-v", f"{Path(root).resolve()}:/candidate:ro",
                   "-v", f"{scratch}:/scratch:rw",
                   "-v", f"{HERE / 'c_preprocessor.py'}:/c_preprocessor.py:ro",
                   "--entrypoint", "python3"]
        if cpuset:
            command += ["--cpuset-cpus", cpuset]
        command += [image, "/c_preprocessor.py", "/scratch/config.json"]
        run = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if run.returncode:
            raise ValueError("C/C++ interface preprocessing failed: " + run.stderr[-4000:])
        result = json.loads((scratch / "preprocessed.json").read_text())
        if "unavailable" in result:
            from .c_preprocessor import Unavailable
            raise Unavailable(result["unavailable"])
        return result


def declarations(root, config, preprocess=None, diagnostics=None):
    if config["language"] == "rust":
        from .rust_surface import declarations as rust_declarations
        return rust_declarations(root, config, diagnostics)
    if config["language"] == "go":
        from .go_surface import declarations as go_declarations
        return go_declarations(root, config, diagnostics)
    if config["language"] in ("c", "cpp"):
        from .c_preprocessor import preprocess as local_preprocess, Unavailable
        try:
            expanded = (preprocess or local_preprocess)(root, config)
        except Unavailable as error:
            if diagnostics is not None:
                diagnostics.update(mode="raw_unavailable", reason=str(error))
        else:
            if diagnostics is not None:
                diagnostics.update(expanded["diagnostics"])
            raw = expanded.get("raw_headers", [])
            tokens, namespaces = set(), set()
            values = c_header_declarations(expanded, allow_empty=bool(raw),
                                           public_tokens=tokens, namespaces=namespaces)
            if diagnostics is not None:
                diagnostics["public_tokens"] = sorted(tokens)
                diagnostics["public_namespaces"] = sorted(namespaces)
            if raw:
                values.update(raw_declarations(root, {**config, "files": raw}, allow_empty=True))
            if not values:
                raise ValueError("No public declarations matched configured C/C++ interface")
            return values
    return raw_declarations(root, config)


def raw_declarations(root, config, allow_empty=False):
    values = {}
    language = config["language"]
    for pattern in config["files"]:
        for path in sorted(Path(root).glob(pattern)):
            if not path.is_file():
                continue
            text = path.read_text(errors="replace")
            if language in ("python", "javascript", "typescript", "java"):
                values.update(structured_declarations(text, language, str(path.relative_to(root))))
                continue
            # Public declaration identity, not a function body fingerprint.
            from .runtime_state import code_only
            suffix = {"rust": ".rs", "go": ".go", "c": ".c", "cpp": ".cpp"}[language]
            if language == "c" and path.suffix in (".hpp", ".cpp", ".cc"):
                suffix = ".cpp"
            original = text
            text = code_only(text, suffix)
            if language == "rust":
                regex = r"\bpub\s+(?:(?:unsafe|async|const|extern\s*)\s+)*(?:fn|struct|enum|trait|type|const|static|mod|use)\s+[^;{]+|\bpub\s+[a-zA-Z_]\w*\s*:\s*[^,}\n]+"
            elif language == "go":
                regex = r"(?m)^(?:func\s+(?:\([^)]*\)\s+)?[A-Z]\w*\s*(?:\[[^\]]*\])?\s*\([^;{]*|type\s+[A-Z]\w*\s+[^;{]+|(?:var|const)\s+[A-Z]\w*[^=\n]*)"
            else:
                original = re.sub(r"\bLZMA_API\s*\(([^()]*)\)", r"\1", original)
                text = code_only(original, suffix)
                regex = r"(?m)^[ \t]*(?:(?:extern|[A-Z_]+\([^;\n]*?\)|[A-Z_]+)\s+)*[A-Za-z_][\w \t*]*?\b[A-Za-z_]\w*\s*\([^;{}]*\)\s*(?:\w+(?:\([^;{}]*\))?\s*)*;"
            for match in re.finditer(regex, text):
                # Locate declarations in masked code, but preserve literals in
                # the actual signature and normalize with the language grammar.
                from .lexical import parse
                declaration = original[match.start():match.end()]
                ending = "{}" if language in ("go", "rust") and ";" not in declaration else ""
                tree, data = parse(declaration + ending, suffix)
                normalized = declaration_tokens(tree.root_node, data, language).strip()
                if normalized:
                    # A declaration moved between files is still the same public item.
                    values[normalized] = True
            if language in ("c", "cpp"):
                # Preserve exposed C aggregate layout as well as function declarations.
                for match in re.finditer(r"\btypedef\s+(?:struct|union|enum)\b[^;{]*\{", text):
                    depth, end = 1, match.end()
                    while depth and end < len(text):
                        depth += (text[end] == "{") - (text[end] == "}")
                        end += 1
                    semicolon = text.find(";", end)
                    if not depth and semicolon >= 0:
                        values[re.sub(r"\s+", " ", text[match.start():semicolon + 1]).strip()] = True
    if not values and not allow_empty:
        raise ValueError("No public declarations matched configured " + language + " interface")
    return values


def structured_declarations(text, language, filename):
    """Static fixture/source surfaces; deployed Python/JS packages still use probes."""
    values = {}
    def put(key, value):
        if key in values:
            previous = values[key]
            values[key] = {"kind": value["kind"], "overloads": [
                *previous.get("overloads", [previous.get("signature")]), value["signature"]]}
        else:
            values[key] = value
    if language == "python":
        module = ast.parse(text)
        exports = None
        for node in module.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
                exports = ast.literal_eval(node.value)
                if not isinstance(exports, (list, tuple)) or not all(isinstance(n, str) for n in exports):
                    raise ValueError("Static Python __all__ must contain literal names")
        for node in module.body:
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and
                    (node.name in exports if exports is not None else not node.name.startswith("_"))):
                signature = ("(" + ast.unparse(node.args) + ")" +
                             (" -> " + ast.unparse(node.returns) if node.returns else "")
                             if hasattr(node, "args") else "class")
                values[filename + ":" + node.name] = {"kind": type(node).__name__, "signature": signature}
                if isinstance(node, ast.ClassDef):
                    for member in node.body:
                        name = getattr(member, "name", None)
                        if name and not name.startswith("_") and isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            values[filename + ":" + node.name + "." + name] = {
                                "kind": "method", "signature": "(" + ast.unparse(member.args) + ")"}
                        elif isinstance(member, (ast.Assign, ast.AnnAssign)):
                            targets = member.targets if isinstance(member, ast.Assign) else [member.target]
                            for target in targets:
                                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                                    values[filename + ":" + node.name + "." + target.id] = {
                                        "kind": "field", "signature": "field " + (
                                            ast.unparse(member.annotation) if isinstance(member, ast.AnnAssign) else "")}
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
                exports = ast.literal_eval(node.value)
                if not isinstance(exports, (list, tuple)) or not all(isinstance(n, str) for n in exports):
                    raise ValueError("Static Python __all__ must contain literal names")
                for name in exports:
                    values[filename + ":export:" + name] = True
        return values
    from .lexical import parse, signature_text
    suffix = {"javascript": ".js", "typescript": ".ts", "java": ".java"}[language]
    if language == "typescript" and filename.endswith(".tsx"):
        suffix = ".tsx"
    tree, data = parse(text, suffix)
    if tree.root_node.has_error:
        raise ValueError("Invalid " + language + " source for public interface extraction")

    def raw(node):
        return data[node.start_byte:node.end_byte].decode()

    def header(node):
        body = node.child_by_field_name("body")
        end = body.start_byte if body else node.end_byte
        return " ".join(declaration_tokens(child, data, language) for child in node.children
                        if child.end_byte <= end and child.type != "comment").strip()

    if language == "java":
        def visit(node, parent="", accessible=True):
            modifiers = next((c for c in node.children if c.type == "modifiers"), None)
            public = modifiers is not None and any(c.type in ("public", "protected") for c in modifiers.children)
            access = ("protected" if modifiers is not None and
                      any(c.type == "protected" for c in modifiers.children) else "public")
            def public_header():
                body = node.child_by_field_name("body")
                pieces = []
                for child in node.children:
                    if child == body:
                        break
                    if child.type == "modifiers":
                        pieces.extend(declaration_tokens(modifier, data, "java")
                                      for modifier in child.children if modifier.type not in ("public", "protected"))
                    else:
                        pieces.append(declaration_tokens(child, data, "java"))
                return " ".join(value for value in pieces if value)
            name = node.child_by_field_name("name")
            class_like = node.type in ("class_declaration", "interface_declaration", "enum_declaration",
                                       "record_declaration", "annotation_type_declaration")
            if class_like and not public:
                accessible = False
            if not accessible:
                return
            if public:
                if node.type == "field_declaration":
                    field_type = node.child_by_field_name("type")
                    for variable in node.named_children:
                        if variable.type == "variable_declarator":
                            field_name = variable.child_by_field_name("name")
                            put(filename + parent + ":" + raw(field_name), {
                                "kind": "field", "signature": raw(field_type),
                                "visibility": access,
                                "writable": not any(c.type == "final" for c in modifiers.children)})
                    return
                identity = parent + ":" + (raw(name) if name else header(node))
                # Overloads are separate public contracts, not last-name-wins.
                put(filename + identity, {"kind": node.type, "signature": public_header(), "visibility": access})
                if node.type == "class_declaration":
                    body = node.child_by_field_name("body")
                    if not any(c.type == "constructor_declaration" for c in body.named_children):
                        put(filename + "." + raw(name) + ":" + raw(name), {
                            "kind": "constructor_declaration", "signature": raw(name) + " ( )", "visibility": access})
            for child in node.named_children:
                visit(child, parent + ("." + raw(name) if name and class_like else ""), accessible)
        visit(tree.root_node)
        return values
    for node in tree.root_node.named_children:
        if node.type != "export_statement":
            continue
        declaration = node.child_by_field_name("declaration")
        if declaration is not None and declaration.type == "ambient_declaration":
            declaration = next((child for child in declaration.named_children if child.type != "comment"), None)
        if any(child.type == "default" for child in node.children):
            value = declaration or node.child_by_field_name("value")
            if value is not None and value.type in ("function_declaration", "function_expression", "arrow_function", "class_declaration", "class"):
                signature = javascript_signature(raw(value), language=language)
            elif value is not None and value.type == "identifier":
                raise ValueError("Static default alias needs a runtime export probe")
            else:
                signature = raw(value) if value else "expression"
            put(filename + ":export:default", {"kind": "default", "signature": signature})
        elif declaration:
            named = ([declaration] if declaration.child_by_field_name("name") else
                     [child for child in declaration.named_children if child.type == "variable_declarator"])
            for item in named:
                name = item.child_by_field_name("name")
                if name and not raw(name).startswith("_"):
                    # Variable implementation/initializer changes are not API removals.
                    value = item.child_by_field_name("value") if item.type == "variable_declarator" else item
                    callable_types = {"function_declaration", "function_signature", "function_expression", "arrow_function",
                                      "generator_function_declaration", "class_declaration", "class"}
                    signature = (javascript_signature(raw(value), language=language)
                                 if value is not None and value.type in callable_types else
                                 raw(item) if item.type in ("interface_declaration", "enum_declaration") else
                                 header(item) if item.type != "variable_declarator" else
                                 raw(name) + (raw(item.child_by_field_name("type")) if item.child_by_field_name("type") else ""))
                    public_kind = ("class" if value is not None and value.type in ("class", "class_declaration")
                                   else "callable" if value is not None and value.type in callable_types else item.type)
                    put(filename + ":export:" + raw(name), {
                        "kind": public_kind, "signature": signature})
                    if item.type in ("class", "class_declaration"):
                        class_key = filename + ":export:" + raw(name)
                        members = item.child_by_field_name("body").named_children
                        constructor = next((member for member in members
                                            if member.child_by_field_name("name") is not None and
                                            raw(member.child_by_field_name("name")) == "constructor"), None)
                        constructor_access = next((raw(child) for child in constructor.named_children
                                                   if child.type == "accessibility_modifier"), "public") if constructor else "public"
                        values[class_key]["constructible"] = constructor_access == "public"
                        if constructor_access == "private":
                            values[class_key]["signature"] = None
                        for method in item.child_by_field_name("body").named_children:
                            member = method.child_by_field_name("name") or method.child_by_field_name("property")
                            access = next((raw(child) for child in method.named_children
                                           if child.type == "accessibility_modifier"), "public")
                            explicit_public = any(child.type == "accessibility_modifier" and raw(child) == "public"
                                                  for child in method.named_children)
                            visible = (member and not raw(member).startswith("#") and
                                       (not raw(member).startswith("_") or explicit_public) and access != "private")
                            if (method.type in ("method_definition", "method_signature", "abstract_method_signature")
                                    and visible):
                                method_signature = javascript_signature(raw(method), language=language)
                                put(filename + ":export:" + raw(name) + "." + raw(member), {
                                    "kind": "method", "signature": method_signature, "visibility": access})
                            elif method.type in ("public_field_definition", "field_definition") and visible:
                                annotation = method.child_by_field_name("type")
                                put(class_key + "." + raw(member), {
                                    "kind": "field", "signature": raw(annotation) if annotation else "field",
                                    "visibility": access,
                                    "writable": not any(child.type == "readonly" for child in method.children)})
        else:
            clause = next((c for c in node.named_children if c.type == "export_clause"), None)
            if clause:
                for item in clause.named_children:
                    name = item.child_by_field_name("alias") or item.child_by_field_name("name")
                    if name and not raw(name).startswith("_"):
                        values[filename + ":export:" + raw(name)] = {"kind": "named-export"}
            else:
                values[filename + ":reexport:" + raw(node)] = True
    return values


def docker_surface(build, config, image, cpuset):
    with tempfile.TemporaryDirectory(prefix="interface-") as temporary:
        scratch = Path(temporary)
        scratch.chmod(0o777)
        (scratch / "config.json").write_text(json.dumps(config))
        command = ["docker", "run", "--rm", "--network", "none", "--user", "10000:10000",
                   "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                   "--pids-limit=512", "--memory=4g", "--cpuset-cpus", cpuset,
                   "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m",
                   "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "HOME=/tmp",
                   "-v", f"{Path(build).resolve()}:/candidate:ro",
                   "-v", f"{scratch}:/scratch:rw",
                   "-v", f"{HERE / 'interface_probe.py'}:/interface_probe.py:ro"]
        if config["kind"] == "python":
            command += ["-v", f"{HERE / 'probe_lifecycle.py'}:/probe_lifecycle.py:ro",
                        "--entrypoint", config.get("python", "python3"), image,
                        "/probe_lifecycle.py", "/interface_probe.py", "/scratch/config.json"]
        else:
            command += ["-v", f"{HERE / 'javascript_interface_probe.cjs'}:/interface_probe.cjs:ro",
                        "-v", f"{HERE / 'interface_acorn.cjs'}:/interface_acorn.cjs:ro",
                        "--entrypoint", "node", image, "/interface_probe.cjs", "/scratch/config.json"]
        run = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if run.returncode:
            raise ValueError("Interface introspection failed: " + run.stderr[-3000:])
        result = json.loads((scratch / "interface.json").read_text())
        return javascript_signatures(result) if config["kind"] == "javascript" else result


def javascript_signatures(surface):
    meta = surface.get("$meta", {})
    if meta.get("format") != "javascript-public-graph-v3":
        raise ValueError("Unsupported JavaScript public surface graph")
    if meta.get("truncated") or any(isinstance(value, dict) and value.get("truncated")
                                    for value in surface.values()):
        raise ValueError("Incomplete JavaScript public surface: explicit traversal truncation")
    result = {}
    for name, value in surface.items():
        value = dict(value)
        if "callable_source" in value:
            raise ValueError("Public surface must not retain full callable source")
        if "formal_shape" in value:
            value["signature"] = json.dumps(value["formal_shape"], sort_keys=True, separators=(",", ":"))
        result[name] = value
    return result


def javascript_graph_lookup(surface, path):
    """Resolve alias prefixes without expanding diamonds into duplicate paths."""
    nodes = {tuple(json.loads(key)): value for key, value in surface.items() if key != "$meta"}
    return _graph_lookup(nodes, tuple(path))


def _graph_lookup(nodes, path):
    seen = set()
    while True:
        if path in seen:
            raise ValueError("Cyclic public interface alias")
        seen.add(path)
        for size in range(len(path), 0, -1):
            row = nodes.get(path[:size])
            if row is not None and "alias_of" in row:
                path = tuple(json.loads(row["alias_of"])) + path[size:]
                break
        else:
            return nodes.get(path), path


def _compare_javascript(left, right):
    old_meta, new_meta = left.get("$meta", {}), right.get("$meta", {})
    removed, changed = [], []
    if old_meta.get("format") != new_meta.get("format") or old_meta.get("limits") != new_meta.get("limits"):
        changed.append("$meta")
    incomplete = bool(old_meta.get("truncated") or new_meta.get("truncated") or
                      any(value.get("truncated") for value in [*left.values(), *right.values()]))
    baseline = {tuple(json.loads(key)): value for key, value in left.items() if key != "$meta"}
    candidate = {tuple(json.loads(key)): value for key, value in right.items() if key != "$meta"}
    identities, reverse = {}, {}
    for path in sorted(baseline):
        key = json.dumps(path, separators=(",", ":"))
        old, old_identity = _graph_lookup(baseline, path)
        new, new_identity = _graph_lookup(candidate, path)
        if new is None:
            removed.append(key)
            continue
        if old is None or _row_changed(old, new):
            changed.append(key)
            continue
        if old.get("kind") in ("object", "function"):
            if ((old_identity in identities and identities[old_identity] != new_identity) or
                    (new_identity in reverse and reverse[new_identity] != old_identity)):
                changed.append(key)
            identities[old_identity], reverse[new_identity] = new_identity, old_identity
    return {"passed": not removed and not changed and not incomplete,
            "removed": removed, "changed": sorted(set(changed)), "incomplete": incomplete,
            "added": sorted(set(right) - set(left)),
            "changed_details": [
                {"public_path": key,
                 "old": _graph_lookup(baseline, tuple(json.loads(key)))[0],
                 "new": _graph_lookup(candidate, tuple(json.loads(key)))[0]}
                for key in sorted(set(changed)) if key != "$meta"],
            "baseline_items": len(baseline), "candidate_items": len(candidate),
            "baseline_aliases": old_meta.get("aliases", 0), "candidate_aliases": new_meta.get("aliases", 0)}


def _row_changed(old, new):
    if not isinstance(old, dict):
        return old != new
    return (old.get("kind") != new.get("kind") or old.get("value_kind") != new.get("value_kind")
            or old.get("external_origin") != new.get("external_origin")
            or old.get("visibility") == "public" and new.get("visibility") not in (None, "public")
            or old.get("strict_signature") and not signature_compatible(
                new.get("signature"), old.get("signature"))
            or old.get("builtin") != new.get("builtin")
            or any(not any(signature_compatible(signature, candidate)
                           for candidate in new.get("overloads", [new.get("signature")]))
                   for signature in old.get("overloads", [old["signature"]] if old.get("signature") is not None else []))
            or any(name in old and (name not in new or new[name] > old[name]) for name in ("arity", "length"))
            or any(old.get(name) is True and new.get(name) is not True
                   for name in ("get", "set", "writable", "constructible")))


def signature_compatible(old, new):
    """Conservative backward-call compatibility; opaque signatures stay exact."""
    if old == new:
        return True
    if not isinstance(old, str) or not isinstance(new, str):
        return False
    if old.startswith("{") and new.startswith("{"):
        try:
            return _javascript_compatible(json.loads(old), json.loads(new))
        except (ValueError, TypeError, KeyError):
            return False
    if old.startswith("(") and new.startswith("("):
        try:
            left = ast.parse("def _" + old + ":\n pass").body[0]
            right = ast.parse("def _" + new + ":\n pass").body[0]
            return _python_compatible(left, right)
        except (SyntaxError, ValueError):
            return False
    # Declaration surfaces are compared as canonical keys, not by guessing
    # compiler/ABI compatibility from an opaque signature string.
    return False


def _javascript_compatible(old, new):
    if any(bool(old.get(flag)) != bool(new.get(flag)) for flag in ("async", "generator")):
        return False
    for flag in ("return_type", "generics", "opaque_builtin"):
        if old.get(flag) != new.get(flag):
            return False
    if "opaque_native_arity" in old or "opaque_builtin" in old:
        key = "opaque_native_arity" if "opaque_native_arity" in old else "arity"
        return key in new and new[key] <= old[key]
    def normalized_kind(shape):
        kind = shape.get("kind")
        return "class" if kind in ("class", "implicit_class") else "function"
    if normalized_kind(old) != normalized_kind(new):
        return False
    def argument(value):
        optional, rest, annotation, default = False, False, None, None
        if isinstance(value, list) and value[0] in ("required_parameter", "optional_parameter"):
            optional = value[0] == "optional_parameter" or value[3] is not None
            annotation, default, value = value[2], value[3], value[1]
        if isinstance(value, list) and value[0] == "default":
            optional, default, value = True, value[2], value[1]
        if isinstance(value, list) and value[0] == "rest":
            optional, rest, value = True, True, value[1]
        return value, optional, rest, annotation, default
    left = old.get("parameters", [])
    right = new.get("parameters", [])
    if len(right) < len(left):
        return False
    for original, candidate in zip(left, right):
        binding, optional, rest, annotation, default = argument(original)
        new_binding, new_optional, new_rest, new_annotation, new_default = argument(candidate)
        if (binding != new_binding or rest != new_rest or annotation != new_annotation
                or optional and not new_optional
                or default is not None and default != new_default):
            return False
    return all(argument(value)[1] for value in right[len(left):])


def _python_compatible(old, new):
    def dump(value):
        return ast.dump(value, include_attributes=False) if value is not None else None
    if dump(old.returns) != dump(new.returns):
        return False
    def positions(node):
        params = [*node.args.posonlyargs, *node.args.args]
        defaults = [None] * (len(params) - len(node.args.defaults)) + node.args.defaults
        return [(arg.arg, dump(arg.annotation), dump(default),
                 index < len(node.args.posonlyargs)) for index, (arg, default) in enumerate(zip(params, defaults))]
    left, right = positions(old), positions(new)
    if len(right) < len(left):
        return False
    for (name, annotation, default, positional), (new_name, new_annotation, new_default, new_positional) in zip(left, right):
        if (not positional and name != new_name or annotation != new_annotation or not positional and new_positional
                or default is not None and default != new_default):
            return False
    if any(default is None for _, _, default, _ in right[len(left):]):
        return False
    left_kw = {arg.arg: (dump(arg.annotation), dump(default)) for arg, default in zip(old.args.kwonlyargs, old.args.kw_defaults)}
    right_kw = {arg.arg: (dump(arg.annotation), dump(default)) for arg, default in zip(new.args.kwonlyargs, new.args.kw_defaults)}
    for name, (annotation, default) in left_kw.items():
        if name not in right_kw or annotation != right_kw[name][0] or default is not None and default != right_kw[name][1]:
            return False
    if any(default is None for name, (_, default) in right_kw.items() if name not in left_kw):
        return False
    for kind in ("vararg", "kwarg"):
        before, after = getattr(old.args, kind), getattr(new.args, kind)
        if before is not None and (after is None or dump(before.annotation) != dump(after.annotation)):
            return False
    return True


def javascript_signature(source, arity=None, language="javascript"):
    """Parameter shape (not local identifier spelling), defaults, rest and TS types."""
    from .lexical import parse, signature_text
    if re.fullmatch(r"\s*function[^(\r\n]*\([^)]*\)\s*\{\s*\[native code\]\s*\}\s*", source):
        return json.dumps({"opaque_native_arity": arity}, sort_keys=True)
    suffix = ".ts" if language == "typescript" else ".js"
    candidates = [(source, False), ("(" + source + ")", False), ("({" + source + "})", False),
                  ("class __InterfaceProbe {" + source + "}", True)]
    for candidate, synthetic_class in candidates:
        tree, data = parse(candidate, suffix)
        if not tree.root_node.has_error:
            break
    else:
        raise ValueError("Cannot parse public JavaScript callable signature")
    callable_types = {"function_declaration", "function_signature", "function_expression", "generator_function",
                      "generator_function_declaration", "arrow_function", "method_definition",
                      "class_declaration", "class", "method_signature", "abstract_method_signature"}
    nodes, function = [tree.root_node], None
    while nodes:
        node = nodes.pop()
        if synthetic_class and node.type == "class_declaration":
            nodes.extend(reversed(node.child_by_field_name("body").named_children))
            continue
        if node.type in callable_types:
            function = node
            break
        nodes.extend(reversed(node.named_children))
    if function is None:
        raise ValueError("No callable in public JavaScript source")
    if function.type in ("class_declaration", "class"):
        body = function.child_by_field_name("body")
        methods = [node for node in body.named_children if node.type == "method_definition"]
        function = next((node for node in methods
                         if (name := node.child_by_field_name("name")) is not None
                         and data[name.start_byte:name.end_byte] == b"constructor"), None)
        if function is None:
            return json.dumps({"implicit_constructor_arity": arity}, sort_keys=True)

    def raw(node):
        return signature_text(node, data)

    def parameter(node):
        kind = node.type
        if kind == "identifier":
            return "$"
        if kind == "shorthand_property_identifier_pattern":
            return ["property", raw(node)]
        if kind == "assignment_pattern":
            return ["default", parameter(node.child_by_field_name("left")),
                    raw(node.child_by_field_name("right"))]
        if kind in ("rest_pattern", "rest_parameter"):
            return ["rest", *[parameter(child) for child in node.named_children]]
        if kind in ("required_parameter", "optional_parameter"):
            pattern = node.child_by_field_name("pattern") or next(iter(node.named_children), None)
            annotation = node.child_by_field_name("type")
            value = node.child_by_field_name("value")
            return [kind, parameter(pattern), raw(annotation) if annotation else None,
                    raw(value) if value else None]
        if kind == "pair_pattern":
            return [raw(node.child_by_field_name("key")), parameter(node.child_by_field_name("value"))]
        return [kind, *[parameter(child) for child in node.named_children if child.type != "comment"]]

    params = function.child_by_field_name("parameters")
    single = function.child_by_field_name("parameter")
    arguments = ([parameter(n) for n in params.named_children if n.type != "comment"]
                 if params else [parameter(single)] if single else [])
    def parameter_name(node):
        while node is not None and node.type in ("required_parameter", "optional_parameter", "assignment_pattern", "rest_pattern"):
            node = (node.child_by_field_name("pattern") or node.child_by_field_name("left")
                    or node.child_by_field_name("argument") or next(iter(node.named_children), None))
        return raw(node) if node is not None and node.type == "identifier" else None
    parameter_names = ([parameter_name(n) for n in params.named_children if n.type != "comment"]
                       if params else [parameter_name(single)] if single else [])
    returns = function.child_by_field_name("return_type")
    generics = function.child_by_field_name("type_parameters")
    return json.dumps({"parameters": arguments, "parameter_names": parameter_names,
                       "return_type": raw(returns) if returns else None,
                       "generics": raw(generics) if generics else None,
                       "async": any(child.type == "async" for child in function.children),
                       "generator": "generator" in function.type or any(child.type == "*" for child in function.children)},
                      sort_keys=True)


def _named_c_declarations(values):
    """Stable declaration IDs pair changed signatures; overloads remain contracts."""
    from .lexical import parse, signature_text
    result = {}
    def declarator(node, data):
        if node is None:
            return None
        if node.type in ("identifier", "type_identifier", "field_identifier",
                         "qualified_identifier", "operator_name", "destructor_name"):
            return signature_text(node, data)
        child = node.child_by_field_name("declarator")
        if child is not None:
            return declarator(child, data)
        for child in node.named_children:
            if child.type.endswith("declarator"):
                found = declarator(child, data)
                if found:
                    return found
        return None
    for key, value in values.items():
        match = re.match(r"^([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)?:(.+)$", key) if value is True else None
        identity = None
        if match:
            tree, data = parse(match[2], ".cpp")
            node = next(iter(tree.root_node.named_children), None)
            while node is not None and node.type == "template_declaration":
                node = next((child for child in node.named_children
                             if child.type != "template_parameter_list"), None)
            if node is not None:
                name = declarator(node.child_by_field_name("declarator"), data)
                kind = "typedef" if node.type == "type_definition" else "declaration"
                if name is None:
                    aggregate = next((child for child in [node, *node.named_children] if child.type in (
                        "struct_specifier", "class_specifier", "union_specifier", "enum_specifier")), None)
                    if aggregate is not None:
                        named = aggregate.child_by_field_name("name")
                        name = signature_text(named, data) if named else None
                        kind = aggregate.type
                if name:
                    identity = "c:" + (match[1] or "") + ":" + kind + ":" + name
        if identity is None:
            result[key] = value
            continue
        row = {"kind": "c_declaration", "signature": key, "public_path": identity}
        if identity in result:
            old = result[identity]
            old.setdefault("overloads", [old["signature"]])
            old["overloads"].append(key)
        else:
            result[identity] = row
    return result


def compare(baseline, candidate):
    if "$meta" in baseline or "$meta" in candidate:
        return _compare_javascript(baseline, candidate)
    baseline, candidate = _named_c_declarations(baseline), _named_c_declarations(candidate)
    missing = sorted(set(baseline) - set(candidate))
    changed = []
    identities, reverse = {}, {}
    # Public aliases may move or gain a lexically earlier alias without
    # changing the nominal type graph. Compare referenced types through the
    # old/new public-path bijection, not an implementation's chosen spelling.
    type_reverse = {}
    for key in sorted(baseline.keys() & candidate.keys()):
        old, new = baseline[key], candidate[key]
        if isinstance(old, dict) and isinstance(new, dict) and "_identity" in old:
            type_reverse.setdefault(new.get("_identity"), old["_identity"])
    def type_normalized(row, newer=False):
        if not isinstance(row, dict) or not row.get("_type_refs"):
            return row
        result = dict(row)
        def normalize(value):
            for token, identity in sorted(row["_type_refs"].items(), key=lambda item: -len(item[0])):
                identity = type_reverse.get(identity, identity) if newer else identity
                replacement = "@type[" + hashlib.sha256(identity.encode()).hexdigest() + "]"
                from .lexical import code_only
                visible = value if row.get("kind") == "constructor" else code_only(value, ".rs")
                matches = list(re.finditer(re.escape(token), value))
                for match in reversed(matches):
                    if visible[match.start():match.end()] == token:
                        value = value[:match.start()] + replacement + value[match.end():]
            return value
        result["signature"] = normalize(row["signature"])
        if "overloads" in row:
            result["overloads"] = [normalize(value) for value in row["overloads"]]
        return result
    for key in baseline.keys() & candidate.keys():
        old, new = baseline[key], candidate[key]
        if _row_changed(type_normalized(old), type_normalized(new, True)):
            changed.append(key)
        if isinstance(old, dict) and isinstance(new, dict) and "_identity" in old:
            left, right = old["_identity"], new.get("_identity")
            if (left in identities and identities[left] != right or
                    right in reverse and reverse[right] != left):
                changed.append(key)
            identities[left], reverse[right] = right, left
    return {"passed": not missing and not changed, "removed": missing, "changed": sorted(set(changed)),
            "added": sorted(set(candidate) - set(baseline)),
            "changed_details": [{"public_path": key, "old": baseline[key], "new": candidate[key]}
                                for key in sorted(set(changed))],
            "baseline_items": len(baseline), "candidate_items": len(candidate)}


def python_interface_config(task, packages):
    """Only trusted task metadata declares documentation and extension points."""
    config = json.loads((Path(task) / "task.json").read_text())["interface"]
    if set(config) != {"documentation", "documented_api", "extension_points"}:
        raise ValueError("Python interface requires documentation, documented_api and extension_points")
    if (not isinstance(config["documentation"], list) or not config["documentation"]
            or not all(isinstance(url, str) and url.startswith("https://")
                       for url in config["documentation"])):
        raise ValueError("Python interface requires HTTPS documentation URLs")
    for field in ("documented_api", "extension_points"):
        if not isinstance(config[field], list):
            raise ValueError("Python interface declarations must be lists")
        for entry in config[field]:
            if not isinstance(entry, dict) or set(entry) != {"name", "documentation"}:
                raise ValueError("Python API declaration requires name and documentation")
            name, url = entry["name"], entry["documentation"]
            if (not isinstance(name, str) or not re.fullmatch(
                    r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:(?:[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*|\*)", name)
                    or not any(name.startswith(p + ":") or name.startswith(p + ".") for p in packages)
                    or field == "extension_points" and name.endswith(":*")
                    or not isinstance(url, str) or not url.startswith("https://")):
                raise ValueError("Invalid Python API declaration: " + repr(name))
    return config


def check(task, baseline, candidate, image, cpuset):
    config = json.loads((Path(task) / "tests/judge.json").read_text())["public_interface"]
    if config["kind"] == "binary":
        return {"passed": True, "applicability": "not_applicable", "reason": config["reason"]}
    components = config.get("components", [config])
    reports = []
    header_proofs = {}
    def public_header_proof(root, side):
        key = str(root)
        if key not in header_proofs:
            tokens, namespaces, proven = set(), set(), False
            for declaration in components:
                if declaration["kind"] != "declarations" or declaration.get("language") not in ("c", "cpp"):
                    continue
                detail = {}
                declarations(root, declaration,
                             preprocess=lambda root, cfg: docker_c_preprocess(root, cfg, image, cpuset),
                             diagnostics=detail)
                if detail.get("mode") == "preprocessed" and detail.get("public_tokens"):
                    tokens.update(detail["public_tokens"])
                    namespaces.update(detail.get("public_namespaces", []))
                    proven = True
                else:
                    proven = False
                    break
            header_proofs[key] = (tokens if proven else None, namespaces)
        return header_proofs[key]
    for component in components:
        component = dict(component)
        if component["kind"] == "python":
            component["interface"] = python_interface_config(task, component["packages"])
        kind = component["kind"]
        files = sorted({str(p.relative_to(baseline)) for pattern in component.get("files", [])
                        for p in Path(baseline).glob(pattern) if p.is_file()})
        extraction = {}
        def surface(root, side):
            if kind == "symbols":
                if not files:
                    raise ValueError("Configured public libraries are absent")
                values = {}
                for relative in files:
                    path = contained(root, relative)
                    if path.is_file():
                        dynamic = component.get("dynamic", True)
                        raw_symbols = symbols(path, dynamic)
                        from .native_surface import project
                        tokens, namespaces = (None, ()) if dynamic else public_header_proof(root, side)
                        projected, proof = project(raw_symbols, dynamic=dynamic,
                                                   public_tokens=tokens, namespaces=namespaces)
                        extraction.setdefault(side, {})[relative] = proof
                        values.update({relative + ":" + k: v for k, v in projected.items()})
                return values
            if kind == "declarations":
                detail = extraction.setdefault(side, {})
                inspector_config = dict(component)
                if side == "candidate" and "headers" in extraction.get("baseline", {}):
                    inspector_config["_pristine_header_modes"] = extraction["baseline"]["headers"]
                return declarations(root, inspector_config,
                                    preprocess=lambda root, cfg: docker_c_preprocess(root, cfg, image, cpuset),
                                    diagnostics=detail)
            probe_config = dict(component)
            if kind == "python" and side == "candidate":
                probe_config["_pristine_selection"] = left
            return docker_surface(root, probe_config, image, cpuset)
        # A broken pristine inspector is an invalid environment, never a candidate failure.
        left = surface(baseline, "baseline")
        error = None
        try:
            right = surface(candidate, "candidate")
            if ("headers" in extraction.get("baseline", {}) and
                    extraction.get("candidate", {}).get("mode") == "raw_unavailable"):
                raise ValueError("Candidate cannot downgrade pristine preprocessed declaration coverage")
        except (ValueError, OSError, subprocess.SubprocessError) as exception:
            right, error = {}, str(exception)
        template_proof = None
        if kind == "symbols":
            from .native_surface import project_removed
            left, template_proof = project_removed(left, right, baseline, candidate, image, cpuset)
        report = compare(left, right)
        if kind == "symbols":
            report["symbol_projection"] = extraction
            report["header_template_copy_proof"] = template_proof
        if component.get("language") in ("c", "cpp"):
            report["declaration_extraction"] = extraction
        if error:
            report.update(passed=False, candidate_introspection_error=error)
        report.update(kind=kind, projection_version=PROJECTION_VERSION,
                      baseline_sha256=hashlib.sha256(json.dumps({
                          "projection_version": PROJECTION_VERSION, "surface": left
                      }, sort_keys=True).encode()).hexdigest())
        reports.append(report)
    result = {"passed": all(row["passed"] for row in reports), "components": reports,
              "projection_version": PROJECTION_VERSION}
    if not result["passed"]:
        raise InterfaceBroken(result)
    return result
