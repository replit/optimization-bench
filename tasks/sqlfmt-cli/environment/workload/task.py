"""Original CLI plus direct native batch, using the unchanged shared clock."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
from runner import Case, Step, HarnessError, contained, canonical_json

ENV = {"LC_ALL": "C", "TZ": "UTC"}


def rows(inputs):
    manifest = json.loads((Path(inputs) / "manifest.json").read_text())
    docs = [dict(doc) for batches in manifest["splits"].values()
            for batch in batches for doc in batch["docs"]]
    for number, doc in enumerate(docs):
        doc["path"] = f"input-{number:04d}-{doc['sha256'][:12]}"
        raw = contained(Path(inputs) / "files", doc["sha256"]).read_bytes()
        if len(raw) != doc["size"] or hashlib.sha256(raw).hexdigest() != doc["sha256"]:
            raise HarnessError("Input size/SHA-256 mismatch")
    return docs


def cases(inputs):
    native = native_manifest(inputs)
    cli = rows(inputs)
    # The shared verifier scopes multi-case references through docs[].path.
    # Each input has both stdout and stderr obligations, not only one output.
    return [
        Case("batch", {
            "inputs": cli,
            "docs": [{"path": path} for doc in cli
                     for path in (doc["path"], doc["path"] + ".stderr")],
        }),
        Case("native-batch", {
            "inputs": native["docs"],
            "docs": [{"path": "native-batch.stdout"}, {"path": "native-batch.stderr"}],
        }),
    ]


def native_manifest(inputs):
    path = Path(inputs) / "native-batch.json"
    if not path.exists():
        raise HarnessError("Expanded task requires its native batch manifest")
    value = json.loads(path.read_text())
    if value.get("format") != "sqlfmt-native-batch-v1" or not value.get("docs"):
        raise HarnessError("Invalid native batch manifest")
    identities, hashes = set(), set()
    for doc in value["docs"]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", doc["id"]) or doc["id"] in identities:
            raise HarnessError("Invalid native input identity")
        if not re.fullmatch(r"[a-f0-9]{64}", doc["sha256"]) or doc["sha256"] in hashes:
            raise HarnessError("Duplicate or invalid native content address")
        data = contained(Path(inputs) / "files", doc["sha256"]).read_bytes()
        if len(data) != doc["bytes"] or hashlib.sha256(data).hexdigest() != doc["sha256"]:
            raise HarnessError("Native input checksum mismatch")
        identities.add(doc["id"])
        hashes.add(doc["sha256"])
    return value


def references(inputs):
    docs = rows(inputs)
    refs = [row for doc in docs for row in (
        doc, {"path": doc["path"] + ".stderr", "expect_len": 0,
              "expect_sha": hashlib.sha256(b"").hexdigest()})]
    native = native_manifest(inputs)
    if native:
        refs.extend([
            {"path": "native-batch.stdout", **native["expected"]},
            {"path": "native-batch.stderr", "expect_len": 0,
             "expect_sha": hashlib.sha256(b"").hexdigest()},
        ])
    return refs


def prepare(case, ctx):
    if case.id == "native-batch":
        request = [
            {"id": doc["id"], "sql": base64.b64encode(
                contained(ctx.inputs / "files", doc["sha256"]).read_bytes()).decode("ascii")}
            for doc in case.data["inputs"]
        ]
        (ctx.scratch / "native-request.json").write_bytes(canonical_json(request))
        return [Step([str(ctx.build / "sqlfmt-native")], stdin_file="native-request.json",
                     home_cwd=True, timeout=120)]
    steps = []
    for number, doc in enumerate(case.data["inputs"]):
        directory = ctx.scratch / f"case-{number:04d}"
        directory.mkdir()
        raw = contained(ctx.inputs / "files", doc["sha256"]).read_bytes()
        header, argv, input_, outputs, payload = raw.split(b"\n", 4)
        if header != b"CLI1":
            raise HarnessError("Unknown CLI payload format")
        argv = argv.decode().split("\t")
        if argv[0] != "sqlfmt":
            raise HarnessError("Unexpected CLI program")
        doc["declared_outputs"] = outputs.decode()
        input_ = input_.decode()
        if input_.startswith("file "):
            contained(directory, input_[5:]).write_bytes(payload)
        elif input_ == "tar":
            with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
                # The pinned Go image's Python predates tar extraction filters.
                # These authenticated SQL-only payloads need no links/devices.
                for member in archive.getmembers():
                    contained(directory, member.name)
                    if not member.isfile() and not member.isdir():
                        raise HarnessError("Unexpected non-file SQL payload member")
                archive.extractall(directory)
        elif input_ not in ("stdin", "none"):
            raise HarnessError("Unknown CLI input transport")
        if input_ == "stdin":
            stream = ctx.scratch / f"stdin-{number:04d}"
            stream.write_bytes(payload)
        for path in directory.rglob("*"):
            if not path.is_symlink():
                os.utime(path, (0, 0))
        steps.append(Step([str(ctx.build / "sqlfmt"), *argv[1:]], cwd=directory.name,
                          timeout=120, home_cwd=True,
                          stdin_file=stream.name if input_ == "stdin" else None))
    return steps


def file_record(kind, name, data):
    return f"{kind} {name} {len(data)}\n".encode() + data


def collect(case, ctx, results):
    if case.id == "native-batch":
        if len(results) != 1:
            raise HarnessError("Missing native batch result")
        result = results[0]
        # Preserve failures and partial/invalid/omitted output as observable
        # bytes. Only the unchanged baseline can supply the expected result.
        data = (f"EXIT {result.exit_code}\n".encode()
                + file_record("STDOUT", "-", result.stdout))
        return {"native-batch.stdout": data, "native-batch.stderr": result.stderr}
    if len(results) != len(case.data["inputs"]):
        raise HarnessError("Missing CLI result")
    outputs = {}
    for number, (doc, result) in enumerate(zip(case.data["inputs"], results)):
        directory = ctx.scratch / f"case-{number:04d}"
        code = result.exit_code
        data = (f"EXIT {code}\n".encode() if code is not None and code >= 0 else
                f"SIGNAL {-code if code is not None else -1}\n".encode())
        data += file_record("STDOUT", "-", result.stdout)
        declaration = doc["declared_outputs"]
        names = ([p.relative_to(directory).as_posix() for p in sorted(directory.rglob("*"))
                  if p.is_file() and not p.is_symlink()] if declaration == "TREE" else
                 [] if declaration == "-" else declaration.split(" "))
        for name in names:
            path = contained(directory, name)
            data += (file_record("FILE", name, path.read_bytes()) if path.is_file()
                     else f"FILE {name} MISSING\n".encode())
        outputs[doc["path"]] = data
        outputs[doc["path"] + ".stderr"] = re.sub(
            rb"0x[0-9a-fA-F]{4,}", b"0x_", result.stderr)
    return outputs
