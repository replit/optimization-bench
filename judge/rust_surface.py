"""Source public projection with local Cargo/module/reexport reachability.

This is not macro expansion or a Rust type checker. Immutable external
reexports remain explicitly opaque; local reexports are followed to their
definitions, including workspace path dependencies. Bodies/private fields are
never declaration identities.
"""
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import tomllib

from .lexical import parse, signature_text


def text(node, data):
    return data[node.start_byte:node.end_byte].decode()


def public(node, data):
    return any(child.type == "visibility_modifier" and text(child, data) == "pub"
               for child in node.named_children)


def parts(node, data):
    return tuple("".join(signature_text(node, data).split()).replace("r#", "").split("::"))


def uses(node, data, prefix=()):
    if node.type == "scoped_use_list":
        yield from uses(node.child_by_field_name("list"), data,
                        prefix + parts(node.child_by_field_name("path"), data))
    elif node.type == "use_list":
        for child in node.named_children:
            yield from uses(child, data, prefix)
    elif node.type == "use_as_clause":
        yield prefix + parts(node.child_by_field_name("path"), data), text(
            node.child_by_field_name("alias"), data).replace("r#", "")
    elif node.type == "use_wildcard":
        path = node.named_children[0] if node.named_children else None
        yield prefix + (parts(path, data) if path else ()) + ("*",), "*"
    else:
        path = prefix + parts(node, data)
        if path[-1] == "self":
            path = path[:-1]
        yield path, path[-1]


@dataclass(eq=False)
class Item:
    module: object
    node: object
    data: bytes
    attrs: tuple
    name: str


@dataclass(eq=False)
class Module:
    crate: object
    path: tuple
    file: Path
    directory: Path
    definitions: dict = field(default_factory=dict)
    imports: list = field(default_factory=list)
    implementations: list = field(default_factory=list)
    parsed: bool = False


@dataclass(eq=False)
class Crate:
    manifest: Path
    name: str
    dependencies: dict = field(default_factory=dict)
    modules: dict = field(default_factory=dict)
    root: object = None


class Projection:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.crates = {}
        self.unresolved = set()
        self.external = set()
        self.rows = {}
        self.exported = {}
        self.aliases = {}
        self.alias_targets = {}
        self.member_routes = {}

    def safe(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Rust local source path escapes protected build")
        return path

    def package(self, manifest):
        manifest = self.safe(manifest)
        if manifest in self.crates:
            return self.crates[manifest]
        config = tomllib.loads(manifest.read_text())
        package = config.get("package", {})
        name = config.get("lib", {}).get("name", package.get("name", manifest.parent.name)).replace("-", "_")
        crate = Crate(manifest, name)
        self.crates[manifest] = crate
        library = self.safe(manifest.parent / config.get("lib", {}).get("path", "src/lib.rs"))
        if not library.is_file():
            raise ValueError("Configured Rust public interface has no library root: " + str(library))
        crate.root = self.module(crate, (), library, library.parent)
        workspace, workspace_dir = {}, manifest.parent
        for ancestor in [manifest.parent, *manifest.parent.parents]:
            if not ancestor.is_relative_to(self.root):
                break
            path = ancestor / "Cargo.toml"
            if path.is_file():
                found = tomllib.loads(path.read_text()).get("workspace", {}).get("dependencies", {})
                if found:
                    workspace, workspace_dir = found, ancestor
                    break
        dependencies = dict(config.get("dependencies", {}))
        for target in config.get("target", {}).values():
            dependencies.update(target.get("dependencies", {}))
        for alias, value in dependencies.items():
            base = manifest.parent
            if isinstance(value, dict) and value.get("workspace"):
                value, base = workspace.get(alias, {}), workspace_dir
            if isinstance(value, dict) and value.get("path"):
                path = (base / value["path"] / "Cargo.toml").resolve()
                if path.is_relative_to(self.root) and path.is_file():
                    crate.dependencies[alias.replace("-", "_")] = self.package(path)
        return crate

    def module(self, crate, path, file, directory, body=None, data=None):
        if path in crate.modules:
            return crate.modules[path]
        module = Module(crate, path, self.safe(file), self.safe(directory))
        crate.modules[path] = module
        if not path:
            crate.root = module
        if body is None:
            tree, data = parse(module.file.read_text(), ".rs")
            body = tree.root_node
        attrs = []
        for node in body.named_children:
            if node.type == "attribute_item":
                attrs.append(text(node, data))
                continue
            if node.type.endswith("comment") or node.type == "comment":
                continue
            current_attrs, attrs = tuple(attrs), []
            if any(re.sub(r"\s+", "", value) == "#[cfg(test)]" for value in current_attrs):
                continue
            if node.type == "use_declaration":
                argument = node.child_by_field_name("argument")
                module.imports.extend((source, alias, public(node, data))
                                      for source, alias in uses(argument, data))
            elif node.type == "extern_crate_declaration":
                match = re.search(r"extern\s+crate\s+(\w+)(?:\s+as\s+(\w+))?", text(node, data))
                if match:
                    name, alias = match[1], match[2] or match[1]
                    ref = crate.root if name == "self" else ("external", name)
                    module.definitions.setdefault(alias, []).append((ref, public(node, data)))
            elif node.type == "impl_item":
                module.implementations.append(Item(module, node, data, current_attrs, "impl"))
            else:
                name = node.child_by_field_name("name")
                if name is None:
                    continue
                name = text(name, data).replace("r#", "")
                item = Item(module, node, data, current_attrs, name)
                if node.type == "mod_item":
                    inline = node.child_by_field_name("body")
                    if inline is not None:
                        child = self.module(crate, path + (name,), file, directory / name, inline, data)
                    else:
                        override = next((re.search(r'path\s*=\s*("(?:[^"\\]|\\.)*")', value)
                                         for value in current_attrs if re.search(r"\bpath\s*=", value)), None)
                        choices = ([directory / json.loads(override[1])] if override else
                                   [directory / (name + ".rs"), directory / name / "mod.rs"])
                        chosen = next((p for p in choices if p.is_file()), None)
                        if chosen is None:
                            # Platform/macro-generated modules remain explicit, not
                            # silently promoted to a complete compiled API.
                            self.unresolved.add("::".join((crate.name, *path, name)))
                            continue
                        child = self.module(crate, path + (name,), chosen, directory / name)
                    module.definitions.setdefault(name, []).append((child, public(node, data)))
                else:
                    visible = public(node, data)
                    if node.type == "macro_definition" and any("macro_export" in value for value in current_attrs):
                        crate.root.definitions.setdefault(name, []).append((item, True))
                    module.definitions.setdefault(name, []).append((item, visible))
        module.parsed = True
        return module

    def lookup(self, module, path, seen=None, external_ok=True):
        seen = set() if seen is None else set(seen)
        key = (module, path)
        if not path or key in seen:
            return []
        seen.add(key)
        head, tail = path[0], path[1:]
        if head == "crate":
            return self.lookup(module.crate.root, tail, seen, external_ok=False)
        if head == "self":
            return self.lookup(module, tail, seen, external_ok=False)
        if head == "super":
            parent = module.crate.modules.get(module.path[:-1], module.crate.root)
            return self.lookup(parent, tail, seen, external_ok=False)
        targets = [ref for ref, _ in module.definitions.get(head, [])]
        if not targets and head in module.crate.dependencies:
            targets = [module.crate.dependencies[head].root]
        if not targets:
            for source, alias, _ in module.imports:
                if alias == head:
                    targets.extend(self.lookup(module, source, seen))
                elif alias == "*":
                    for source_module in self.lookup(module, source[:-1], seen):
                        if isinstance(source_module, Module):
                            targets.extend(self.exports(source_module, seen).get(head, []))
        if not targets:
            return [("external", "::".join(path))] if external_ok else []
        if not tail:
            return targets
        found = []
        for ref in targets:
            if isinstance(ref, Module):
                found.extend(self.lookup(ref, tail, seen, external_ok=False))
            elif isinstance(ref, tuple):
                found.append(("external", ref[1] + "::" + "::".join(tail)))
        return found

    def exports(self, module, seen=None):
        seen = set() if seen is None else set(seen)
        marker = (module, ("*exports*",))
        if marker in seen:
            return {}
        seen.add(marker)
        result = {name: [ref for ref, visible in refs if visible]
                  for name, refs in module.definitions.items() if any(visible for _, visible in refs)}
        for source, alias, visible in module.imports:
            if not visible:
                continue
            targets = self.lookup(module, source[:-1] if alias == "*" else source, seen)
            if alias == "*":
                for ref in targets:
                    if isinstance(ref, Module):
                        for name, refs in self.exports(ref, seen).items():
                            result.setdefault(name, []).extend(refs)
                    elif isinstance(ref, tuple):
                        result.setdefault("*", []).append(("external", ref[1] + "::*"))
                        self.external.add(ref[1] + "::*")
            elif targets:
                result.setdefault(alias, []).extend(targets)
            else:
                # External dependencies are immutable protected image inputs.
                # Retain their alias/target identity without inventing members.
                external = ("opaque_local", "::".join(source))
                result.setdefault(alias, []).append(external)
                self.unresolved.add("::".join(source))
        return result

    def reach(self, module, path, ancestors=()):
        if module in ancestors:
            return
        for name, refs in sorted(self.exports(module).items()):
            for ref in refs:
                route = path + (name,)
                if isinstance(ref, Module):
                    self.exported.setdefault(ref, []).append(route)
                    self.reach(ref, route, ancestors + (module,))
                else:
                    self.exported.setdefault(ref, []).append(route)

    def canonical(self, item, node=None, name=None, extra=None):
        from .interfaces import declaration_tokens
        aliases = dict(self.aliases.get(item.module, {}))
        aliases.update(extra or {})
        return declaration_tokens(node or item.node, item.data, "rust",
                                  type_aliases=aliases, name_override=name)

    def identity(self, item):
        return "::".join((str(item.module.crate.manifest.relative_to(self.root)),
                          *item.module.path, item.name, item.node.type))

    def add(self, route, item, kind, signature, identity=None):
        path = "::".join(route)
        origin = (str(item.module.file.relative_to(self.root))
                  if isinstance(item, Item) else None)
        row = {"kind": kind, "signature": signature, "public_path": path,
               "stable_id": hashlib.sha256(("rust|" + path + "|" + kind).encode()).hexdigest()}
        if origin is not None:
            row["origin"] = origin
        row["_identity"] = identity or (
            self.identity(item) + (":constructor" if kind == "constructor" else "")
            if isinstance(item, Item) else repr(item))
        previous = self.rows.get(path)
        if previous and previous["signature"] != signature:
            previous.setdefault("overloads", [previous["signature"]])
            if signature not in previous["overloads"]:
                previous["overloads"].append(signature)
        else:
            self.rows[path] = row

    def fields(self, item, body, route, enum=False):
        if body is None:
            return [], True
        data, values, all_public = item.data, [], True
        if body.type == "ordered_field_declaration_list":
            visible, index = enum, 0
            for child in body.named_children:
                if child.type == "visibility_modifier":
                    visible = text(child, data) == "pub" or enum
                    continue
                if child.type.endswith("comment") or child.type == "attribute_item":
                    continue
                signature = self.canonical(item, child)
                if visible:
                    self.add(route + (str(index),), item, "field", signature,
                             identity="::".join(route + ("field", str(index))))
                all_public &= visible
                values.append(signature)
                visible, index = enum, index + 1
        else:
            for child in body.named_children:
                if child.type != "field_declaration":
                    continue
                name = text(child.child_by_field_name("name"), data).replace("r#", "")
                signature = self.canonical(item, child.child_by_field_name("type"))
                visible = enum or public(child, data)
                if visible:
                    self.add(route + (name,), item, "field", signature,
                             identity="::".join(route + ("field", name)))
                all_public &= visible
                values.append((name, signature))
            values.sort()
        return values, all_public

    def emit(self, item, route, constructor_allowed=True):
        if isinstance(item, tuple):
            self.add(route, item, item[0] + "_reexport", item[1], identity=item[1])
            (self.external if item[0] == "external" else self.unresolved).add(item[1])
            return
        if isinstance(item, Module):
            self.add(route, item, "module", "pub mod " + "::".join(route),
                     identity=str(item.crate.manifest.relative_to(self.root)) + repr(item.path))
            return
        node, data = item.node, item.data
        if item in self.alias_targets:
            target = self.alias_targets[item]
            body = target.node.child_by_field_name("body")
            return self.emit(target, route, constructor_allowed=bool(
                body and body.type == "field_declaration_list"))
        kind = node.type.removesuffix("_item")
        body = node.child_by_field_name("body")
        attrs = " ".join(value for value in item.attrs
                         if re.match(r"#\[\s*(?:cfg|repr|non_exhaustive)\b", value))
        if node.type in ("struct_item", "enum_item", "union_item", "trait_item"):
            header = " ".join(signature_text(child, data) for child in node.children
                              if child != body and child.type != ";")
            header = re.sub(r"\b" + re.escape(item.name) + r"\b", route[-1], header, count=1)
            if node.type == "struct_item":
                kind = ("tuple_struct" if body and body.type == "ordered_field_declaration_list"
                        else "record_struct" if body else "unit_struct")
                values, constructor = self.fields(item, body, route)
                if constructor and constructor_allowed and not any("non_exhaustive" in value for value in item.attrs):
                    self.add(route + ("constructor",), item, "constructor",
                             "rust constructor " + json.dumps(values))
            elif node.type == "enum_item" and body:
                for variant in body.named_children:
                    if variant.type != "enum_variant":
                        continue
                    name = text(variant.child_by_field_name("name"), data)
                    self.add(route + (name,), item, "variant", self.canonical(item, variant))
            elif node.type == "trait_item" and body:
                for member in body.named_children:
                    name = member.child_by_field_name("name")
                    if name is not None:
                        self.add(route + (text(name, data),), item, "trait_member",
                                 self.canonical(item, member),
                                 identity=self.identity(item) + ":member:" + text(name, data))
            self.add(route, item, kind, header + " " + attrs)
        elif node.type == "macro_definition":
            patterns = []
            for rule in node.named_children:
                if rule.type != "macro_rule":
                    continue
                pattern = rule.child_by_field_name("left") or rule.named_children[0]
                value = signature_text(pattern, data)
                value = re.sub(r"\$\s*[A-Za-z_]\w*(?=\s*:)", "$arg", value)
                patterns.append("rust macro " + value)
            self.add(route, item, "macro", patterns[0] if patterns else "rust macro opaque")
            self.rows["::".join(route)]["overloads"] = sorted(set(patterns))
        else:
            self.add(route, item, kind, self.canonical(item, name=route[-1]) + " " + attrs)

    def finish(self, roots):
        for module in roots:
            self.reach(module, (module.crate.name,))
        self.member_routes = {ref: list(routes) for ref, routes in self.exported.items()}
        # A local type alias exposes the target's fields/inherent methods too,
        # not merely the spelling of its `pub type` statement.
        for ref, routes in list(self.exported.items()):
            if not isinstance(ref, Item) or ref.node.type != "type_item":
                continue
            target = ref.node.child_by_field_name("type")
            if target is not None and target.type in ("type_identifier", "scoped_type_identifier"):
                found = self.lookup(ref.module, parts(target, ref.data))
                if found and isinstance(found[0], Item):
                    self.alias_targets[ref] = found[0]
                    self.member_routes.setdefault(found[0], []).extend(routes)
        for ref, routes in self.member_routes.items():
            if isinstance(ref, Item):
                preferred = "::".join(sorted(routes)[0])
                self.aliases.setdefault(ref.module, {})[ref.name] = preferred
        # Resolve local imported type aliases in each implementation's lexical
        # module before canonicalizing method parameter/result types.
        for crate in self.crates.values():
            for module in crate.modules.values():
                aliases = self.aliases.setdefault(module, {})
                for source, alias, _ in module.imports:
                    if alias == "*":
                        continue
                    found = self.lookup(module, source)
                    if found and found[0] in self.exported:
                        aliases[alias] = "::".join(sorted(self.exported[found[0]])[0])
                    elif found and isinstance(found[0], tuple):
                        aliases[alias] = found[0][1]
        for ref, routes in list(self.exported.items()):
            for route in sorted(set(routes)):
                self.emit(ref, route)
        for crate in self.crates.values():
            for module in crate.modules.values():
                for implementation in module.implementations:
                    node, data = implementation.node, implementation.data
                    owner = node.child_by_field_name("type")
                    if owner is None:
                        continue
                    base = owner.child_by_field_name("type") if owner.type == "generic_type" else owner
                    owners = self.lookup(module, parts(base or owner, data))
                    for ref in owners:
                        if ref not in self.member_routes or not isinstance(ref, Item):
                            continue
                        trait = node.child_by_field_name("trait")
                        if trait is not None:
                            base_trait = (trait.child_by_field_name("type")
                                          if trait.type == "generic_type" else trait)
                            traits = self.lookup(module, parts(base_trait or trait, data))
                            if any(isinstance(ref, Item) and ref not in self.exported for ref in traits):
                                continue  # A private local trait is not an external capability.
                        target = node.child_by_field_name("body")
                        if target is None:
                            continue
                        for route in sorted(set(self.member_routes[ref])):
                            extra = {"Self": "::".join(route)}
                            if trait is not None:
                                self.add(route + ("impl[" + signature_text(trait, data) + "]",),
                                         implementation, "trait_impl",
                                         "rust impl " + self.canonical(implementation, trait, extra=extra))
                            for method in target.named_children:
                                name = method.child_by_field_name("name")
                                if name is not None and ((trait is None and public(method, data)) or
                                                         trait is not None and "function" not in method.type):
                                    self.add(route + (text(name, data),), implementation,
                                             "method" if "function" in method.type else "associated_item",
                                             self.canonical(implementation, method, extra=extra),
                                             identity=self.identity(ref) + ":member:" + text(name, data))
        type_tokens = {"::".join(sorted(routes)[0]): self.identity(ref)
                       for ref, routes in self.member_routes.items()
                       if isinstance(ref, Item) and ref.node.type in (
                           "struct_item", "enum_item", "trait_item", "type_item", "union_item")}
        for row in self.rows.values():
            signature = row["signature"] + " ".join(row.get("overloads", []))
            refs = ({token: identity for token, identity in type_tokens.items() if token in signature}
                    if row["kind"] != "macro" else {})
            if refs:
                row["_type_refs"] = refs
        if not self.rows:
            raise ValueError("No externally reachable Rust declarations")
        return self.rows


def declarations(root, config, diagnostics=None):
    root = Path(root).resolve()
    projection = Projection(root)
    manifests = set()
    files = sorted({path for pattern in config["files"] for path in root.glob(pattern) if path.is_file()})
    for path in files:
        for parent in path.parents:
            if not parent.is_relative_to(root):
                break
            if (parent / "Cargo.toml").is_file():
                manifests.add(parent / "Cargo.toml")
                break
    if not manifests:
        # Grammar-only fixtures still use real visibility/field projection.
        crate = Crate(root / "Cargo.toml", "crate")
        projection.crates[crate.manifest] = crate
        for index, path in enumerate(files):
            module = projection.module(crate, () if not index else (path.stem,), path, path.parent)
            crate.root = crate.root or module
        roots = list(crate.modules.values())
    else:
        roots = [projection.package(path).root for path in sorted(manifests)]
    result = projection.finish(roots)
    if diagnostics is not None:
        diagnostics.update(format="rust-public-projection-v1",
                           local_crates=len(projection.crates),
                           external_reexports=sorted(projection.external),
                           unresolved_modules=sorted(projection.unresolved),
                           compiler_proven_complete=False)
    return result
