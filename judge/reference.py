"""Admit immutable pristine builds made by the candidate build pipeline.

The image's original source, frozen output contracts and upstream identities
remain independent anchors. Never regenerate those anchors to admit a build.
"""
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile

from . import build, heldout, policy, upstream
from .leftovers import resident_workers

FORMAT = "judge-rebuilt-reference-v1"
ROOT = Path(__file__).resolve().parents[1]


class QualificationError(ValueError):
    """An original-source reference failed task qualification, not a submission."""


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def descriptor(task, trusted, image, public_only=False):
    task, trusted = Path(task), Path(trusted)
    rules = json.loads((task / "release.json").read_text())["source_policy"]
    admission = policy.envelope(trusted / "pristine", trusted / "pristine", rules)
    # The image commits its toolchain and protected recipe. Host admission/build
    # helpers and task configuration can change without changing that image.
    helpers = [p for base in (ROOT / "judge", ROOT / "tools/runner")
               for p in sorted(base.rglob("*"))
               if p.is_file() and not {"tests", "__pycache__"} & set(p.relative_to(base).parts)
               and p.suffix in (".py", ".c", ".h", ".cjs", ".mjs", ".json", ".lock")]
    # Hardware attestations are linked by session eligibility, not part of a
    # rebuilt library's semantic admission. Including them would make adding
    # the qualification receipt invalidate the reference that produced it.
    host_data = ROOT / "judge/hosts"
    helpers = [p for p in helpers if not (
        p == host_data / "registry.json" or
        p.is_relative_to(host_data / "qualifications"))]
    if (ROOT / "tools/payload_check.py").is_file():
        helpers.append(ROOT / "tools/payload_check.py")
    configuration = [task / "release.json", task / "task.json", task / "task.toml",
                     task / "payload.json", task / "environment/payload_check.py",
                     *[p for p in sorted((task / "tests").rglob("*"))
                       if p.is_file() and "heldout" not in p.relative_to(task / "tests").parts
                       and "__pycache__" not in p.parts],
                     *[p for p in sorted((task / "environment/workload").rglob("*"))
                       if p.is_file() and p.suffix in (".py", ".sh", ".c", ".h", ".cjs", ".mjs")],
                     task / "environment/workload/build.sh",
                     trusted / "public-workload/inputs/manifest.json"]
    from .interfaces import PROJECTION_VERSION
    return {
        "format": FORMAT, "task": task.name, "image": image,
        "interface_projection_version": PROJECTION_VERSION,
        "heldout_status": ("not-used:public-only" if public_only else
                           json.loads((task / "tests/heldout.lock.json").read_text())["status"]),
        **({"public_only": True} if public_only else {}),
        "source_sha256": policy.source_digest(admission),
        "helpers": {str(p.relative_to(ROOT)): _sha(p) for p in helpers},
        "configuration": {("image-public-manifest" if p.is_relative_to(trusted) else
                           str(p.relative_to(task))): _sha(p)
                          for p in configuration if p.is_file()},
    }


def cache_key(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _modes(root):
    return cache_key({str(p.relative_to(root)): stat.S_IMODE(p.lstat().st_mode)
                      for p in sorted(Path(root).rglob("*"))})


def verify(root, expected=None):
    """Verify all immutable commitments on every cache read; no silent repair."""
    from .verifier import build_inventory
    root = Path(root)
    if root.is_symlink() or any((root / name).is_symlink() for name in
                               ("reference.json", "admission.json", "built/build-receipt.json")):
        raise QualificationError("Symlink in rebuilt reference provenance")
    manifest = json.loads((root / "reference.json").read_text())
    value = manifest["descriptor"]
    if (manifest["format"] != FORMAT or manifest["key"] != cache_key(value)
            or (expected is not None and value != expected)):
        raise QualificationError("Rebuilt reference cache identity mismatch")
    if any((root / name).is_symlink() for name in ("built", "built/build", "built/source")):
        raise QualificationError("Rebuilt reference roots cannot be symlinks")
    if build_inventory(root / "built/build") != manifest["build_sha256"]:
        raise QualificationError("Rebuilt reference inventory changed")
    if build_inventory(root / "built/source") != manifest["source_inventory_sha256"]:
        raise QualificationError("Rebuilt reference source changed")
    if (_modes(root / "built/build") != manifest["build_modes_sha256"]
            or _modes(root / "built/source") != manifest["source_modes_sha256"]):
        raise QualificationError("Rebuilt reference permissions changed")
    if _sha(root / "built/build-receipt.json") != manifest["build_receipt_sha256"]:
        raise QualificationError("Rebuilt reference build receipt changed")
    if _sha(root / "admission.json") != manifest["admission_sha256"]:
        raise QualificationError("Rebuilt reference admission receipt changed")
    receipt = json.loads((root / "built/build-receipt.json").read_text())
    if any(receipt.get(k) != v for k, v in {
            "task": value["task"], "image": value["image"],
            "source_sha256": value["source_sha256"],
            "build_sha256": manifest["build_sha256"],
            "compiler_cache_seed": None}.items()):
        raise QualificationError("Rebuilt reference build provenance mismatch")
    admission = json.loads((root / "admission.json").read_text())
    if (admission.get("key") != manifest["key"]
            or admission.get("build_sha256") != manifest["build_sha256"]
            or admission.get("source_sha256") != value["source_sha256"]
            or admission.get("image") != value["image"]
            or not admission.get("public_outputs", {}).get("passed")
            or (not value.get("public_only") and value["heldout_status"] == "available" and
                not (admission.get("heldout_outputs") or {}).get("passed"))
            or not admission.get("upstream", {}).get("passed")):
        raise QualificationError("Rebuilt reference was not admitted")
    return {"root": root, "trusted": Path(manifest["trusted"]),
            "build": root / "built/build", "key": manifest["key"],
            "build_sha256": manifest["build_sha256"],
            "source_sha256": value["source_sha256"],
            "upstream": admission["upstream"]["suites"], "manifest": manifest}


def verify_for(task, trusted, image, reference, public_only=False):
    """Recompute provenance rather than trusting a caller's reference dictionary."""
    return verify(reference["root"], descriptor(task, trusted, image, public_only=public_only))


def validate_call(task, image, baseline, reference):
    if reference is None:
        raise QualificationError("Admitted rebuilt reference required; use judge.reference")
    verified = verify(reference["root"])
    if verified["manifest"]["descriptor"].get("public_only"):
        raise QualificationError("Public-only gate reference cannot admit a measurement session")
    verified = verify_for(task, verified["trusted"], image, verified)
    if Path(baseline).resolve() != verified["build"].resolve():
        raise QualificationError("Baseline is not the admitted pristine rebuild")
    return verified


def outputs(task, folder, inputs, projects, image, cpuset, private):
    from .verifier import HARDENING, load_task
    from runner import Context, fingerprint, references, run_case
    from docker_executor import DockerExecutor
    settings = json.loads((task / "tests/judge.json").read_text())
    module = load_task(task)
    references_ = list(references(module, inputs))
    expected = {row["path"]: (row["expect_len"], row["expect_sha"]) for row in references_}
    if not expected or len(expected) != len(references_):
        raise QualificationError("Missing or duplicate frozen reference outputs")
    observed, records = {}, []
    cases = list(module.cases(inputs))
    if not cases or len({case.id for case in cases}) != len(cases):
        raise QualificationError("Missing or duplicate reference cases")
    for case in cases:
        with tempfile.TemporaryDirectory(prefix="judge-reference-case-") as temporary, \
                heldout.workload_view(task / "tests", task / "environment/workload",
                                      enabled=private) as workload:
            ctx = Context(Path(temporary), folder, inputs, projects, workload, seed=0, docker=True)
            executor = DockerExecutor(image, cpuset, memory=settings["memory"],
                pids_limit=settings["pids_limit"], extra_docker_flags=HARDENING,
                tmpfs="/tmp:rw,nosuid,nodev,size=2g", task=task)
            data, steps = run_case(module, case, ctx, executor)
            workers = resident_workers(steps)
            if workers:
                raise QualificationError(f"Pristine reference has unfinished resident workers on case {case.id}: {workers}")
            actual = {name: fingerprint(raw, getattr(module, "RESULT_ENCODING", None))
                      for name, raw in data.items()}
            if observed.keys() & actual.keys():
                raise QualificationError("Reference cases duplicate output identities")
            observed.update(actual)
            records.append({"case": case.id, "outputs": actual,
                            "exits": [step.exit_code for step in steps]})
    if observed != expected:
        failed = sorted(name for name in observed.keys() | expected.keys()
                        if observed.get(name) != expected.get(name))
        raise QualificationError("Pristine reference differs from frozen " +
                                 ("held-out" if private else "public") + " outputs: " + ", ".join(failed))
    return {"passed": True, "cases": records, "timing_performed": False}


@heldout.acquired
def ensure(task, trusted, image, cpuset, cache, execution_host=None, public_only=False):
    """Build once per protocol key and publish only after independent admission."""
    from . import gates, interfaces
    from .gate_contracts import require_baseline
    from .verifier import HarnessError, build_inventory, image_id, public_input_path
    task, trusted, cache = map(lambda p: Path(p).resolve(), (task, trusted, cache))
    image = image_id(image)
    value = descriptor(task, trusted, image, public_only=public_only)
    key = cache_key(value)
    parent = cache / FORMAT / task.name / image.split(":")[-1]
    parent.mkdir(parents=True, exist_ok=True)
    root = parent / key
    # A distinct immutable namespace leaves the legacy image cache untouched.
    with (parent / (key + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if root.exists():
            return verify(root, value)
        with tempfile.TemporaryDirectory(prefix="reference-", dir=parent) as temporary:
            stage = Path(temporary) / "entry"
            stage.mkdir()
            try:
                built = build.build(task, trusted / "pristine", image, cpuset, stage / "built",
                                    source=trusted / "pristine", execution_host=execution_host)
                folder = stage / "built/build"
                # No-op rebuilt source must preserve the image's public API.
                interface = interfaces.check(task, trusted / "baseline", folder, image, cpuset)
                public_inputs = public_input_path(task, trusted / "public-workload/inputs")
                public = outputs(task, folder, public_inputs, trusted / "projects",
                                 image, cpuset, private=False)
                private = None
                status = "not-used:public-only" if public_only else heldout.check(task / "tests")["status"]
                if status == "available":
                    # Reference admission never adds private projects or
                    # receipts to the legacy image asset cache.
                    with tempfile.TemporaryDirectory(prefix="projects-", dir=stage) as temporary_projects:
                        projects = Path(temporary_projects) / "projects"
                        shutil.copytree(trusted / "projects", projects, symlinks=True)
                        heldout.fetch_archives(task / "tests", projects)
                        projects = heldout.project_root(task / "tests", projects)
                        private = outputs(task, folder, task / "tests/heldout", projects,
                                          image, cpuset, private=True)
                # Read a legacy image identity ledger if present, but never
                # write to, invalidate or repurpose that cache.
                original = trusted / "upstream-reference.json"
                if original.exists():
                    anchor = json.loads(original.read_text())
                    if ((anchor["image"], anchor["source_sha256"], anchor["build_sha256"]) !=
                            (image, value["source_sha256"], build_inventory(trusted / "baseline"))):
                        raise QualificationError("Image upstream anchor commitments differ")
                    original_suites = anchor["suites"]
                else:
                    original_suites = gates.execute_suites(
                        task, trusted / "pristine", trusted / "baseline", image, cpuset,
                        stage / "image-upstream", cache / FORMAT / "compiler-cache")
                require_baseline(task.name, task, original_suites)
                actual = gates.execute_suites(task, trusted / "pristine", folder, image, cpuset,
                                              stage / "reference-upstream", cache / FORMAT / "compiler-cache")
                require_baseline(task.name, task, actual)
                upstream.compare(original_suites, actual, task.name)
                admission = {"key": key, "image": image, "source_sha256": value["source_sha256"],
                    "build_sha256": built["build_sha256"], "public_outputs": public,
                    "heldout_outputs": private, "public_interface": interface,
                    "upstream": {"passed": True, "suites": actual,
                                 "image_anchor_suites": original_suites}, "claimable": False}
                (stage / "admission.json").write_bytes(_json(admission) + b"\n")
                manifest = {"format": FORMAT, "key": key, "descriptor": value,
                    "trusted": str(trusted),
                    "build_sha256": built["build_sha256"],
                    "source_inventory_sha256": build_inventory(stage / "built/source"),
                    "build_modes_sha256": _modes(stage / "built/build"),
                    "source_modes_sha256": _modes(stage / "built/source"),
                    "build_receipt_sha256": _sha(stage / "built/build-receipt.json"),
                    "admission_sha256": _sha(stage / "admission.json")}
                (stage / "reference.json").write_bytes(_json(manifest) + b"\n")
                verify(stage, value)
                stage.rename(root)
            except (ValueError, OSError, KeyError, HarnessError) as error:
                raise QualificationError("Pristine task qualification failed: " + str(error)) from error
    return verify(root, value)


def main(argv=None):
    import argparse
    from . import public_input_preflight, score
    from .verifier import image_id
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--cpuset", default="0-7")
    parser.add_argument("--cache", type=Path, default=Path("/tmp/optimbench-score-cache"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError("Reference descriptor output must be fresh")
    image = image_id(args.image)
    trusted = score.assets(args.task.resolve(), image, args.cache.resolve())
    public_input_preflight.ensure_public_inputs(args.task, source=trusted / "public-workload")
    result = ensure(args.task, trusted, image, args.cpuset, args.cache)
    # This is an admission descriptor, not a performance result.
    value = {"root": str(result["root"]), "build": str(result["build"]),
             "key": result["key"], "build_sha256": result["build_sha256"],
              "claimable": False, "descriptor_format": "rebuilt-reference-cli-v2",
              "execution": {"mode": "untimed_reference_admission", "timing_performed": False,
                            "runtime_image_id": image, "requested_cpuset": args.cpuset,
                            "verified_cpuset": None,
                            "host_qualification": "not_attested_by_reference_cli",
                            "claimable": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps(value, sort_keys=True))
    print("Reference admitted: " + args.task.name + " / " + result["key"] +
          " (untimed; not a host-qualified performance result)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
