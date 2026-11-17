"""Build a task-image overlay without editing the production Dockerfile."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .receipt import image, sha256

HERE = Path(__file__).resolve().parent
PRIMARY = {"python": ["python", "--version"], "javascript": ["node", "--version"],
           "javascript-source": ["node", "--version"],
           "rust-sanitizers": ["rustc", "--version"],
           "go": ["go", "version"], "rust": ["rustc", "--version"]}


def local_parent(ref):
    """Dockerfile FROM cannot portably consume a daemon-local config digest.

    Bind it to a verified retained local tag and use the same daemon's legacy
    builder, not a remote BuildKit worker that might pull an unrelated tag.
    Builders without this local mode fail unsupported; a real OCI manifest
    digest remains the explicit distribution-only alternative.
    """
    image(ref)
    if not ref.startswith("sha256:"):
        return ref, {}, {"kind": "OCI-manifest-reference", "requested": ref}
    actual = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", ref], text=True).strip()
    if actual != ref:
        raise ValueError("daemon local parent identity mismatch")
    tag = "optimbench-fuzz-parent:sha256-" + ref.split(":", 1)[1]
    subprocess.run(["docker", "tag", ref, tag], check=True, capture_output=True)
    check = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag], text=True).strip()
    if check != actual:
        raise ValueError("local parent tag did not bind requested immutable image")
    return tag, {"DOCKER_BUILDKIT": "0"}, {
        "kind": "verified-daemon-local-tag", "requested": ref, "resolved_image_id": actual,
        "dockerfile_from": tag, "local_tag_retained": True,
        "builder": "same-daemon legacy builder; no registry fallback or OS/toolchain substitution"}


def version(ref, argv):
    return subprocess.check_output(
        ["docker", "run", "--rm", "--network=none", "--cap-drop=ALL",
         "--security-opt=no-new-privileges", "--entrypoint", argv[0], ref, *argv[1:]],
        stderr=subprocess.STDOUT, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=["native", "python", "javascript", "javascript-source", "go", "rust", "rust-sanitizers"], required=True)
    parser.add_argument("--task-image", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    image(args.task_image)
    if args.out.exists():
        raise ValueError("use a fresh output directory")
    args.out.mkdir(parents=True)
    parent_ref, build_env, parent_binding = local_parent(args.task_image)
    parent_version = version(args.task_image, PRIMARY[args.language]) if args.language in PRIMARY else None
    recipe = HERE / "images" / (args.language + ".Dockerfile")
    with tempfile.TemporaryDirectory(prefix="fuzz-image-context-") as directory:
        context = Path(directory)
        shutil.copytree(HERE, context / "tools/fuzz_runners",
                        ignore=shutil.ignore_patterns("wheels", "node_modules", "target", "__pycache__", "receipts"))
        argv = ["docker", "build", "--pull=false", "--build-arg", "TASK_IMAGE=" + parent_ref,
                "-f", str(recipe), "-t", args.tag, str(context)]
        with (args.out / "build.log").open("wb") as log:
            result = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT, env={**os.environ, **build_env})
    record = {"schema": "optimbench-runner-image-v1", "language": args.language,
              "parent_task_image": args.task_image, "recipe_sha256": sha256(recipe),
              "build_argv": argv, "build_log_sha256": sha256(args.out / "build.log"),
              "parent_binding": parent_binding, "build_environment_overrides": build_env,
              "exit_code": result.returncode, "parent_primary_toolchain": parent_version,
              "status": "unsupported" if result.returncode else "built-unvalidated"}
    if result.returncode:
        record["reason"] = "overlay build failed; inspect bound build log; do not change task toolchain implicitly"
    else:
        record["runner_image"] = subprocess.check_output(
            ["docker", "image", "inspect", "--format", "{{.Id}}", args.tag], text=True).strip()
        if parent_version is not None:
            built_version = version(record["runner_image"], PRIMARY[args.language])
            record["runner_primary_toolchain"] = built_version
            if built_version != parent_version:
                record.update(status="unsupported", reason="task primary toolchain changed in overlay")
    if parent_binding["kind"] == "verified-daemon-local-tag":
        after = subprocess.check_output(
            ["docker", "image", "inspect", "--format", "{{.Id}}", parent_ref], text=True).strip()
        if after != args.task_image:
            record.update(status="unsupported", reason="parent tag changed during overlay build")
    record["dependency_locks"] = {
        p.relative_to(HERE).as_posix(): sha256(p) for p in
        [HERE / "python.lock", HERE / "package-lock.json", HERE / "rust/Cargo.lock", HERE / "go/go.sum"]}
    (args.out / "image.json").write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
    print(json.dumps(record))
    return 0 if record["status"] == "built-unvalidated" else 2


if __name__ == "__main__":
    raise SystemExit(main())
