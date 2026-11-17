"""Go source public projection; local aliases/embedding, not compiler proof."""
from collections import defaultdict
from pathlib import Path
import re

from .lexical import parse, signature_text


def exported(name):
    return bool(name) and name[0].isupper()


def raw(node, data):
    return data[node.start_byte:node.end_byte].decode()


def first_type(node, data):
    if node is None:
        return None
    if node.type == "type_identifier":
        return raw(node, data)
    for child in node.named_children:
        found = first_type(child, data)
        if found:
            return found
    return None


def declarations(root, config, diagnostics=None):
    from .interfaces import declaration_tokens
    root = Path(root).resolve()
    packages = defaultdict(lambda: {"types": {}, "functions": [], "methods": defaultdict(list), "values": []})
    for path in sorted({p for pattern in config["files"] for p in root.glob(pattern) if p.is_file()}):
        if path.name.endswith("_test.go"):
            continue
        tree, data = parse(path.read_text(), ".go")
        clause = next((n for n in tree.root_node.named_children if n.type == "package_clause"), None)
        if clause is None:
            raise ValueError("Go source has no package clause")
        package = raw(clause.named_children[0], data)
        prefix = package + "/" + path.parent.relative_to(root).as_posix()
        for parent in path.parents:
            if not parent.is_relative_to(root):
                break
            manifest = parent / "go.mod"
            if manifest.is_file():
                match = re.search(r"(?m)^module\s+([^\s]+)", manifest.read_text())
                if match:
                    suffix = path.parent.relative_to(parent).as_posix()
                    prefix = match[1] + ("/" + suffix if suffix != "." else "")
                break
        imports = {}
        for node in tree.root_node.named_children:
            if node.type == "import_declaration":
                specs = [n for n in node.named_children if n.type == "import_spec"]
                specs += [s for n in node.named_children if n.type == "import_spec_list" for s in n.named_children]
                for spec in specs:
                    target = spec.child_by_field_name("path")
                    if target is not None:
                        value = raw(target, data).strip('"`')
                        name = spec.child_by_field_name("name")
                        imports["package:" + (raw(name, data) if name else value.rsplit("/", 1)[-1])] = value
        info = packages[prefix]
        info["package"] = package
        for node in tree.root_node.named_children:
            if node.type == "type_declaration":
                for spec in node.named_children:
                    name = spec.child_by_field_name("name")
                    if name is not None:
                        info["types"][raw(name, data)] = (spec, data, imports)
            elif node.type == "function_declaration":
                info["functions"].append((node, data, imports))
            elif node.type == "method_declaration":
                owner = first_type(node.child_by_field_name("receiver"), data)
                info["methods"][owner].append((node, data, imports))
            elif node.type in ("var_declaration", "const_declaration"):
                info["values"].extend((spec, data, imports) for spec in node.named_children
                                      if spec.type in ("var_spec", "const_spec"))
    rows = {}
    for prefix, info in packages.items():
        if "/internal/" in "/" + prefix + "/":
            continue
        if info["package"] == "main":
            rows[prefix + ":CLI"] = {"kind": "binary", "signature": "Go main is not an importable package"}
            continue
        types, methods = info["types"], info["methods"]
        def put(path, kind, signature, identity=None):
            rows[prefix + ":" + path] = {"kind": kind, "signature": signature,
                                         "public_path": prefix + ":" + path}
            if identity:
                rows[prefix + ":" + path]["_identity"] = prefix + ":" + identity
        def canonical(node, data, imports, aliases=None, name=None):
            return declaration_tokens(node, data, "go", type_aliases={**imports, **(aliases or {})},
                                      name_override=name)
        for node, data, imports in info["functions"]:
            name = raw(node.child_by_field_name("name"), data)
            if exported(name):
                put(name, "function", canonical(node, data, imports))
        for name, (spec, data, imports) in types.items():
            if not exported(name):
                continue
            target, source_name, seen = spec, name, set()
            while target.type == "type_alias":
                next_name = first_type(target.child_by_field_name("type"), data)
                if next_name not in types or next_name in seen:
                    break
                seen.add(next_name)
                target, data, imports = types[next_name]
                source_name = next_name
            body = target.child_by_field_name("type")
            if body is None:
                continue
            aliases = {source_name: name}
            if body.type not in ("struct_type", "interface_type"):
                put(name, "type", canonical(target, data, imports, aliases, name=name), identity=source_name)
                continue
            # Collect selector candidates by embedding depth. Ambiguous promoted
            # selectors are not public calls; direct selectors shadow promotions.
            selectors = defaultdict(list)
            def collect(owner, depth=0, trail=()):
                if owner in trail or owner not in types:
                    return
                definition, source, local_imports = types[owner]
                value = definition.child_by_field_name("type")
                if value is None:
                    return
                owner_aliases = {owner: name}
                for method, method_data, method_imports in methods.get(owner, []):
                    member = raw(method.child_by_field_name("name"), method_data)
                    if exported(member):
                        selectors[member].append((depth, "method",
                            canonical(method, method_data, method_imports, owner_aliases)))
                container = next((n for n in value.named_children if n.type == "field_declaration_list"), value)
                for field in container.named_children:
                    if field.type == "method_elem":
                        member = raw(field.child_by_field_name("name"), source)
                        if exported(member):
                            selectors[member].append((depth, "method",
                                canonical(field, source, local_imports, owner_aliases)))
                    elif field.type == "field_declaration":
                        members = field.children_by_field_name("name")
                        field_type = field.child_by_field_name("type")
                        tag = field.child_by_field_name("tag")
                        signature = canonical(field_type, source, local_imports, owner_aliases)
                        if not members and raw(field, source).lstrip().startswith("*"):
                            signature = "* " + signature
                        if tag is not None:
                            signature += " " + signature_text(tag, source)
                        if not members:
                            embedded = first_type(field_type, source)
                            if embedded:
                                collect(embedded, depth + 1, trail + (owner,))
                                if exported(embedded):
                                    selectors[embedded].append((depth, "field", signature))
                        for member in members:
                            member = raw(member, source)
                            if exported(member):
                                selectors[member].append((depth, "field", signature))
                    elif field.type == "type_elem":
                        embedded = first_type(field, source)
                        if embedded in types:
                            collect(embedded, depth + 1, trail + (owner,))
                        else:
                            put(name + ".embed[" + canonical(field, source, local_imports) + "]",
                                "interface_embedding", canonical(field, source, local_imports))
            collect(source_name)
            parameters = target.child_by_field_name("type_parameters")
            signature = "type " + name + " " + (
                canonical(parameters, data, imports) + " " if parameters else "") + body.type
            put(name, body.type, signature, identity=source_name)
            if body.type == "struct_type":
                fields = next((child for child in body.named_children
                               if child.type == "field_declaration_list"), None)
                literal, public_fields = [], True
                for field in fields.named_children if fields else []:
                    if field.type != "field_declaration":
                        continue
                    members = field.children_by_field_name("name")
                    field_type = field.child_by_field_name("type")
                    names = ([raw(member, data) for member in members] or
                             [first_type(field_type, data) or ""])
                    public_fields &= all(exported(member) for member in names)
                    literal.extend([canonical(field_type, data, imports, aliases)] * len(names))
                if public_fields:
                    put(name + ".literal", "constructor", "Go positional literal " + repr(literal))
            for member, candidates in selectors.items():
                shortest = min(row[0] for row in candidates)
                nearest = [row for row in candidates if row[0] == shortest]
                if len(nearest) == 1:
                    _, kind, signature = nearest[0]
                    put(name + "." + member, kind, signature)
        for spec, data, imports in info["values"]:
            for name in spec.children_by_field_name("name"):
                name = raw(name, data)
                if exported(name):
                    put(name, "value", canonical(spec, data, imports))
    if not rows:
        raise ValueError("No externally visible Go declarations")
    if diagnostics is not None:
        diagnostics.update(format="go-public-projection-v1", compiler_proven_complete=False)
    return rows
