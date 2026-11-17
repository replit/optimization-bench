#!/usr/bin/env python3
"""Bind public upstream/header/runtime evidence without reading held-out data."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    files = [p for p in sorted(args.source.rglob("*")) if p.is_file()
             and ".git" not in p.relative_to(args.source).parts]
    headers = [p.relative_to(args.source).as_posix() for p in files
               if p.suffix.lower() in (".h", ".hh", ".hpp", ".hxx", ".pxd")]
    manifests = [p for p in files if p.name in ("Cargo.toml", "pyproject.toml", "setup.cfg", "setup.py")]
    evidence = {}
    for relative in (
        "pyproject.toml", "README.md", "LICENSE.md",
        "docs/source/reference/internals/index.rst",
        "docs/source/reference/internals/reflow.rst",
        "docs/source/guides/contributing/architecture.rst",
        "src/sqlfluff/utils/reflow/config.py",
        "src/sqlfluff/core/parser/match_result.py",
    ):
        path = args.source / relative
        if path.is_file():
            evidence[relative] = {"sha256": digest(path),
                                  "source_url": "https://github.com/sqlfluff/sqlfluff/blob/"
                                  "0348222a3bcdb0226563f7349c5a39beb0641955/" + relative}
    source_manifest = [[p.relative_to(args.source).as_posix(), digest(p)] for p in files]
    runtime_program = """
import importlib.util,inspect,json
import sqlfluff
from sqlfluff.cli.commands import cli
symbols={}
for name in sqlfluff.__all__:
 function=getattr(sqlfluff,name)
 signature=inspect.signature(function)
 symbols[name]={'origin_module':function.__module__,'qualname':function.__qualname__,
  'parameters':[{'name':p.name,'kind':p.kind.name,
                 'required':p.default is inspect.Parameter.empty,
                 'default':None if p.default is inspect.Parameter.empty else p.default,
                 'annotation':None if p.annotation is inspect.Parameter.empty else inspect.formatannotation(p.annotation)}
                for p in signature.parameters.values()],
  'return_annotation':None if signature.return_annotation is inspect.Signature.empty
                      else inspect.formatannotation(signature.return_annotation)}
commands={}
for name,command in sorted(cli.commands.items()):
 commands[name]=[{'name':p.name,'opts':p.opts,'secondary_opts':getattr(p,'secondary_opts',[]),
                  'required':p.required,'type':str(p.type)}
                 for p in command.params]
print(json.dumps({'version':sqlfluff.__version__,
 'dialects':[dict(row._asdict()) for row in sqlfluff.list_dialects()],
 'top_level_exports':list(sqlfluff.__all__),
 'top_level_signatures':{name:str(inspect.signature(getattr(sqlfluff,name)))
                         for name in sqlfluff.__all__},
 'top_level_structural_symbols':symbols,
 'cli_commands':commands,
 'optional_sqlfluffrs_installed':importlib.util.find_spec('sqlfluffrs') is not None},sort_keys=True))
"""
    image_id = subprocess.check_output(["docker", "image", "inspect", args.image,
                                        "--format", "{{.Id}}"], text=True).strip()
    raw = subprocess.check_output(["docker", "run", "--rm", "--network", "none",
                                   "-e", "PYTHONPATH=/app/baseline-build/runtime",
                                   image_id, "python", "-c", runtime_program], timeout=60)
    runtime = json.loads(raw)
    wheels = list((args.build / "runtime").glob("sqlfluff-*.dist-info/WHEEL"))
    assert len(wheels) == 1
    native_runtime = [p.relative_to(args.build).as_posix() for p in
                      (args.build / "runtime/sqlfluff").rglob("*")
                      if p.suffix.lower() in (".so", ".dll", ".dylib")]
    project = tomllib.loads((args.source / "pyproject.toml").read_text())["project"]
    record = {
        "format": "optimbench-sqlfluff-public-scope-v1",
        "upstream_commit": "0348222a3bcdb0226563f7349c5a39beb0641955",
        "upstream_archive_sha256": "e0afbbc9db5ceda286c660caffa640a6aea92ba99e964b9b7522207e01322a38",
        "task_image": image_id,
        "source_regular_files": len(files),
        "source_manifest_digest": hashlib.sha256(json.dumps(source_manifest, separators=(",", ":")).encode()).hexdigest(),
        "header_inventory": {"extensions": [".h", ".hh", ".hpp", ".hxx", ".pxd"],
                             "paths": headers, "count": len(headers),
                             "status": "source inventory complete; public consumer-matrix scope requires owner review",
                             "guessed_C_Cpp_standards": [],
                             "matrix_pass_claim": False},
        "wheel": {"sha256": digest(wheels[0]), "content": wheels[0].read_text()},
        "native_files_in_sqlfluff_runtime": native_runtime,
        "rust_source_files": sum(p.suffix == ".rs" for p in files),
        "build_manifests": {p.relative_to(args.source).as_posix(): digest(p) for p in manifests},
        "python_requirement": project["requires-python"],
        "platform_python_classifiers": [c for c in project["classifiers"]
                                        if c.startswith(("Operating System", "Programming Language"))],
        "public_evidence": evidence,
        "runtime_inventory": runtime,
        "qualification": "inventory only; optional/native instrumentation applicability remains explicit, not a blanket N/A waiver",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"headers": len(headers), "native_runtime_files": native_runtime,
                      "rust_source_files": record["rust_source_files"],
                      "dialects": len(runtime["dialects"]),
                      "CLI_commands": sorted(runtime["cli_commands"]),
                      "optional_sqlfluffrs_installed": runtime["optional_sqlfluffrs_installed"]}))


if __name__ == "__main__":
    main()
