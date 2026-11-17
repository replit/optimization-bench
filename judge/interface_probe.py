"""Read-only package introspection, executed in an isolated task-image container."""
import contextlib
import importlib
import inspect
import io
import json
from pathlib import Path
import pkgutil
import re
import sys


def signature(value):
    try:
        result = str(inspect.signature(value))
        return re.sub(r"0x[0-9a-fA-F]+", "0xADDRESS", result)
    except (TypeError, ValueError):
        return None


def python_surface(root, packages, interface=None, selection=None):
    """Narrow public API plus explicitly declared subclass/plugin contracts.

    Discovery runs only on pristine. Candidates resolve the same qualified names,
    so private modules need not remain importable and cannot expand the probe.
    """
    interface = interface or {}
    sys.path.insert(0, str(root))
    result = {}
    identities = {}
    identity_objects = []

    def load_module(name):
        module = importlib.import_module(name)
        filename = getattr(module, "__file__", None)
        if filename is None or not Path(filename).resolve().is_relative_to(Path(root).resolve()):
            raise ImportError("Protected module did not load from the selected build: " + name)
        return module

    def owned(value):
        origin = (value.__name__ if inspect.ismodule(value)
                  else getattr(value, "__module__", None))
        if origin is None and hasattr(value, "__objclass__"):
            origin = value.__objclass__.__module__
        if isinstance(value, property) and value.fget is not None:
            origin = value.fget.__module__
        return origin is None or any(origin == p or origin.startswith(p + ".") for p in packages)

    def row(value, scopes, strict=False):
        result = {"kind": "module" if inspect.ismodule(value) else
                "class" if inspect.isclass(value) else "callable" if callable(value) else "value",
                "signature": signature(value) if callable(value) else None,
                "scopes": sorted(scopes), "strict_signature": strict}
        if callable(value) or inspect.ismodule(value):
            # Deterministic encounter labels, not process addresses. The
            # comparator checks the public alias bijection, not label equality.
            if id(value) not in identities:
                identities[id(value)] = "python-object-" + str(len(identities))
                identity_objects.append(value)  # Prevent transient descriptor ID reuse.
            result["_identity"] = identities[id(value)]
        if not owned(value):
            origin = getattr(value, "__module__", None) or getattr(getattr(value, "__objclass__", None), "__module__", None)
            result["external_origin"] = str(origin) + ":" + str(getattr(value, "__qualname__", getattr(value, "__name__", "")))
        return result

    def add(module, name, scope, extension=False):
        value = getattr(module, name)
        if extension and not callable(value):
            raise ValueError("Extension point must be a class or hook: " + module.__name__ + ":" + name)
        if not owned(value) and scope == "top":
            return
        key = module.__name__ + ":" + name
        scopes = set(result.get(key, {}).get("scopes", [])) | {scope}
        result[key] = row(value, scopes, extension or result.get(key, {}).get("strict_signature", False))
        if inspect.isclass(value):
            for member in sorted(dir(value)):
                if member.startswith("_") and not extension:
                    continue
                method = inspect.getattr_static(value, member)
                if isinstance(method, (staticmethod, classmethod)):
                    method = method.__func__
                # Inherited object/stdlib methods are not this library's API.
                if not owned(method):
                    continue
                if member.startswith("_") and not callable(method):
                    continue
                member_key = key + "." + member
                previous = result.get(member_key, {})
                result[member_key] = row(method, set(previous.get("scopes", [])) | {scope},
                                         extension or previous.get("strict_signature", False))

    def resolve(reference):
        module_name, separator, name = reference.partition(":")
        if not separator:
            raise ValueError("Python API reference must be module:qualified_name: " + reference)
        module = load_module(module_name)
        return module, name

    if selection is not None:
        for key, previous in selection.items():
            module_name, separator, path = key.partition(":")
            try:
                module = load_module(module_name)
                if not separator:
                    result[key] = row(module, previous["scopes"])
                    continue
                names = path.split(".")
                exports = getattr(module, "__all__", None)
                exported = names[0] in exports if exports is not None else not names[0].startswith("_")
                if ({"all", "top"} & set(previous["scopes"]) and not exported):
                    continue
                value = getattr(module, names[0])
                explicit = bool({"all", "documented", "extension"} & set(previous["scopes"]))
                if not owned(value) and not explicit:
                    continue
                for name in names[1:]:
                    value = inspect.getattr_static(value, name)
                    if isinstance(value, (staticmethod, classmethod)):
                        value = value.__func__
                if owned(value) or explicit:
                    result[key] = row(value, previous["scopes"], previous["strict_signature"])
            except (ImportError, AttributeError):
                # Missing protected names are precise removals, not a discovery
                # failure that obscures the affected API.
                continue
        return result

    for package in packages:
        top = load_module(package)
        # Capture before walk_packages imports subpackages. Otherwise discovery
        # itself adds incidental top-level attributes that a fresh candidate
        # import cannot be expected to expose.
        result[package] = row(top, {"top"})
        exports = getattr(top, "__all__", None)
        if exports is not None and (not isinstance(exports, (tuple, list)) or
                                    not all(isinstance(name, str) for name in exports)):
            raise ValueError("Python __all__ must be a list/tuple of names: " + package)
        for public in sorted(exports if exports is not None else
                             (name for name in dir(top) if not name.startswith("_"))):
            add(top, public, "all" if exports is not None else "top")
        modules = set()
        if hasattr(top, "__path__"):
            modules |= {name for _, name, _ in pkgutil.walk_packages(top.__path__, package + ".")
                        if all(not part.startswith("_") for part in name.split("."))
                        and not any(part in ("tests", "testing") for part in name.split("."))}
        for name in sorted(modules):
            module = load_module(name)
            exports = getattr(module, "__all__", ())
            if not isinstance(exports, (tuple, list)) or not all(isinstance(n, str) for n in exports):
                raise ValueError("Python __all__ must be a list/tuple of names: " + name)
            for public in sorted(exports):
                add(module, public, "all")
    for field, scope in (("documented_api", "documented"), ("extension_points", "extension")):
        for entry in interface.get(field, []):
            module, name = resolve(entry["name"])
            if name == "*" and field == "documented_api":
                # Explicit Sphinx automodule :members: pages; no imported names.
                for public in sorted(dir(module)):
                    value = getattr(module, public)
                    if (not public.startswith("_") and callable(value)
                            and getattr(value, "__module__", None) == module.__name__):
                        add(module, public, scope)
            elif "." in name:
                parent, *members = name.split(".")
                value = getattr(module, parent)
                for member in members:
                    value = inspect.getattr_static(value, member)
                    if isinstance(value, (staticmethod, classmethod)):
                        value = value.__func__
                if owned(value):
                    result[entry["name"]] = row(value, {scope}, field == "extension_points")
            else:
                add(module, name, scope, extension=field == "extension_points")
    return result


if __name__ == "__main__":
    config = json.loads(Path(sys.argv[1]).read_text())
    # Package import chatter must not contaminate the machine-readable result.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        result = python_surface(Path("/candidate") / config["root"], config["packages"],
                                config.get("interface"), config.get("_pristine_selection"))
    Path("/scratch/interface.json").write_text(json.dumps(result, sort_keys=True))
