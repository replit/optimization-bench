"""Project static-archive vague linkage using authenticated public header text."""
import hashlib
import json
import re
import subprocess
from pathlib import Path
import tempfile

VERSION = "native-public-vague-linkage-v1"


def without_templates(value):
    result, depth = [], 0
    for char in value:
        if char == "<":
            depth += 1
        elif char == ">" and depth:
            depth -= 1
        elif not depth:
            result.append(char)
    return "".join(result)


def project(values, *, dynamic, public_tokens=None, namespaces=()):
    """Never drop dynamic exports, strong symbols, or unproven weak ABI.

    Only Itanium C++ W/V copies whose entity/owner names are absent from the
    *expanded public-origin header text* are excluded. Raw-header fallback is
    insufficient proof. C weak names, ABI allocation hooks, unrecognized
    mangling and entities mentioned by public headers remain protected.
    """
    report = {"version": VERSION, "ignored": {}, "dynamic_exports_preserved": bool(dynamic)}
    if dynamic or public_tokens is None:
        report["scope"] = "all-exports" if dynamic else "unproven-preserve-all"
        return dict(values), report
    tokens, namespaces = set(public_tokens), set(namespaces)
    report.update(scope="public-header-proven-static",
                  public_tokens_sha256=hashlib.sha256(
                      json.dumps(sorted(tokens), separators=(",", ":")).encode()).hexdigest())
    weak = [name for name, kind in values.items() if kind in ("W", "V") and name.startswith("_Z")]
    if not weak:
        return dict(values), report
    decoded = subprocess.run(["c++filt", "-n"], input="\n".join(weak) + "\n",
                             capture_output=True, text=True, check=True)
    names = decoded.stdout.splitlines()
    if len(names) != len(weak):
        raise ValueError("C++ symbol demangler returned an incomplete mapping")
    result = dict(values)
    for raw, pretty in zip(weak, names):
        if pretty == raw or "operator new" in pretty or "operator delete" in pretty:
            continue
        entity = re.sub(r"^(?:(?:non-virtual|virtual) thunk to |guard variable for |"
                        r"vtable for |typeinfo(?: name)? for )", "", pretty)
        entity = without_templates(entity.split("(", 1)[0]).strip().split()
        if not entity:
            continue
        qualified = entity[-1].split("::")
        anchors = [part.removeprefix("~") for part in qualified[-2:]
                   if part.removeprefix("~") not in namespaces]
        if not anchors or any(not re.fullmatch(r"[A-Za-z_]\w*", name) for name in anchors):
            continue
        if any(name in tokens for name in anchors):
            continue
        result.pop(raw)
        report["ignored"][raw] = {"kind": values[raw], "demangled": pretty,
                                  "reason": "vague entity/owner absent from expanded public headers"}
    return result, report


TEMPLATE_COPY_VERSION = "installed-header-template-copy-v1"


def consumer(raw):
    if not raw.startswith("_Z"):
        return None
    text = subprocess.run(["c++filt", "-n", raw], capture_output=True, text=True,
                          check=True, timeout=10).stdout.strip()
    match = re.search(r"(?:^|\s)(?P<owner>[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)::"
                      r"(?P<method>[A-Za-z_]\w*)<(?P<args>[A-Za-z_][\w: ,]*)>"
                      r"\(\)(?P<const> const)?$", text)
    if not match:
        return None
    owner, method, args = match["owner"], match["method"], match["args"]
    types = [owner, *[arg.strip() for arg in args.split(",")]]
    if any(not re.fullmatch(r"[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*", t) for t in types):
        return None
    qualifier = "const " if match["const"] else ""
    return {"demangled": text, "types": types,
            "body": f"auto &optimbench_header_consumer({qualifier}{owner} &x) {{ return x.{method}<{args}>(); }}\n"}


def installed_source(root, plan):
    include = Path(root) / "include"
    if not include.is_dir():
        raise ValueError("Installed public headers absent")
    include = include.resolve()
    headers, consumed = {}, 0
    for path in sorted(include.rglob("*")):
        if path.suffix not in (".h", ".hpp", ".hh", ".hxx") or not path.is_file():
            continue
        if not path.resolve().is_relative_to(include):
            raise ValueError("Installed header escapes include root")
        data = path.read_bytes()
        consumed += len(data)
        if len(data) > 4 << 20 or consumed > 64 << 20:
            raise ValueError("Header proof exceeds bounded source inventory")
        text = data.decode(errors="replace")
        for typename in plan["types"]:
            name = typename.split("::")[-1]
            if re.search(r"\b(?:class|struct)\s+(?:[A-Za-z_]\w*\s+)*" + re.escape(name) +
                         r"\b[^;{}]{0,500}\{", text):
                headers[path.relative_to(include).as_posix()] = hashlib.sha256(data).hexdigest()
    if not headers or len(headers) > 8:
        raise ValueError("No bounded installed class-definition proof")
    return "".join('#include <' + name + '>\n' for name in sorted(headers)) + plan["body"], headers


def compile_definition(root, source, raw, image, cpuset):
    """Compile a public consumer, never link or execute the target library."""
    with tempfile.TemporaryDirectory(prefix="interface-header-template-") as temporary:
        scratch = Path(temporary)
        scratch.chmod(0o777)
        (scratch / "consumer.cpp").write_text(source)
        flags = ["-std=c++17", "-O0", "-fno-inline", "-c"]
        if image is None:  # Local compiler is used by focused source-consumer controls.
            command = ["c++", *flags, "-I" + str(Path(root) / "include"),
                       str(scratch / "consumer.cpp"), "-o", str(scratch / "consumer.o")]
        else:
            command = ["docker", "run", "--rm", "--network", "none", "--read-only",
                       "--user", "10000:10000", "--cap-drop=ALL",
                       "--security-opt=no-new-privileges", "--pids-limit=512", "--memory=4g",
                       "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m",
                       "-v", f"{(Path(root) / 'include').resolve()}:/installed:ro",
                       "-v", f"{scratch}:/scratch:rw"]
            if cpuset:
                command += ["--cpuset-cpus", cpuset]
            command += ["--entrypoint", "c++", image, *flags, "-I/installed",
                        "/scratch/consumer.cpp", "-o", "/scratch/consumer.o"]
        run = subprocess.run(command, capture_output=True, timeout=120)
        if run.returncode:
            raise ValueError("Installed-header consumer could not instantiate the method")
        obj = scratch / "consumer.o"
        defined = subprocess.run(["nm", "-g", "--defined-only", str(obj)],
                                 capture_output=True, text=True, check=True, timeout=10)
        undefined = subprocess.run(["nm", "-u", str(obj)], capture_output=True,
                                   text=True, check=True, timeout=10)
        if not re.search(r"\bW\s+" + re.escape(raw) + r"$", defined.stdout, re.M):
            raise ValueError("Consumer did not emit the exact weak template definition")
        if re.search(r"\b" + re.escape(raw) + r"$", undefined.stdout, re.M):
            raise ValueError("Consumer still depends on the library template definition")
        return {"object_sha256": hashlib.sha256(obj.read_bytes()).hexdigest(),
                "defined_symbol": raw, "kind": "W", "linked_library": False}


def project_removed(left, right, baseline, candidate, image, cpuset, compiler=compile_definition):
    result = dict(left)
    proof = {"version": TEMPLATE_COPY_VERSION, "ignored": {}, "retained_unproven": {}}
    for key in sorted(left.keys() - right.keys()):
        if left[key] != "W":
            continue  # Strong, variable and non-template weak symbols stay protected.
        raw = key.rsplit(":", 1)[-1]
        plan = consumer(raw)
        if plan is None:
            continue
        try:
            arms = {}
            for name, root in (("baseline", baseline), ("candidate", candidate)):
                source, headers = installed_source(root, plan)
                arms[name] = {**compiler(root, source, raw, image, cpuset),
                              "installed_headers": headers,
                              "consumer_sha256": hashlib.sha256(source.encode()).hexdigest()}
            result.pop(key)
            proof["ignored"][key] = {
                "reason": "both installed public headers emit their own exact weak template copy",
                "demangled": plan["demangled"], "arms": arms}
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            proof["retained_unproven"][key] = str(error)
    return result, proof
