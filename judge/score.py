"""One-command provisional scoring on a Docker sandbox separate from the agent."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib

from . import build, gates, heldout, policy, score_host, score_result, upstream, interfaces, behavior, runtime_state, reference, fuzzing_generic, public_input_preflight
from .source_policy import digest
from .gate_contracts import require_baseline
from .process_state import CandidateOutputRejected
from .verifier import ROOT, HarnessError, build_inventory, evaluate, image_id


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def artifacts(task, supplied):
    """Explicit root maps, simple editable-root directories, or Harbor 0.24 trials."""
    supplied = Path(supplied).resolve()
    rules = json.loads((task / "release.json").read_text())["source_policy"]
    file_exports = (rules.get("operations") == ["modify"] and
                    rules["source_roots"] == ["."] and bool(rules.get("allowed_files")))
    roots = rules["allowed_files"] if file_exports else rules["source_roots"]

    def exported_path(path):
        path = Path(path)
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise ValueError("Symlink in artifact export")
        if not (path.is_file() if file_exports else path.is_dir()):
            raise ValueError("Artifact export must be a " + ("file" if file_exports else "directory"))
        return path.resolve()

    descriptor = supplied if supplied.is_file() else next(
        (p for p in [supplied / "submission.json", supplied / "artifacts.json",
                     supplied / "artifacts/submission.json"] if p.is_file()), None)
    if descriptor:
        value = json.loads(descriptor.read_text())
        if not isinstance(value, dict):
            raise ValueError("Artifact descriptor must be a JSON object")
        mapping = value.get("artifacts", value)
        if not isinstance(mapping, dict) or set(mapping) != set(roots):
            raise ValueError("Artifact map must contain exactly the task's declared exports")
        if not all(isinstance(path, str) for path in mapping.values()):
            raise ValueError("Artifact paths must be strings")
        return {root: str(exported_path(descriptor.parent / path))
                for root, path in mapping.items()}, value
    config = tomllib.loads((task / "task.toml").read_text())
    mapping = {}
    if file_exports or roots == ["."]:
        source_root = Path(gates.source_path(task))
    else:
        possible = None
        for root in roots:
            bases = {a["source"][:-len("/" + root)] for a in config["artifacts"]
                     if a["source"].endswith("/" + root)}
            possible = bases if possible is None else possible & bases
        if not possible or len(possible) != 1:
            raise ValueError("Declared Harbor artifacts do not identify one full source-root prefix")
        source_root = Path(possible.pop())
    for root in roots:
        expected_source = str(source_root / root) if root != "." else str(source_root)
        declared = [a for a in config["artifacts"] if str(Path(a["source"])) == expected_source]
        if len(declared) != 1:
            raise ValueError("Cannot map editable root to a unique Harbor artifact: " + root)
        entry = declared[0]
        relative = entry.get("destination") or entry["source"].lstrip("/")
        candidates = [supplied / "artifacts" / relative, supplied / relative, supplied / root]
        if len(roots) == 1 and supplied.name == Path(root).name:
            candidates.append(supplied)
        matches = {exported_path(p) for p in candidates
                   if (p.is_file() if file_exports else p.is_dir())}
        if len(matches) != 1:
            raise ValueError("Missing/ambiguous editable root in submission: " + root)
        mapping[root] = str(matches.pop())
        for manifest in (supplied / "artifacts/manifest.json", supplied / "manifest.json"):
            if manifest.is_file():
                rows = json.loads(manifest.read_text())
                rows = rows if isinstance(rows, list) else rows.get("entries", [])
                selected = [r for r in rows if r.get("source") == entry["source"]]
                if not selected or selected[-1]["status"] != "ok":
                    raise ValueError("Harbor did not successfully collect " + entry["source"])
    return mapping, {}


def ensure_image(task, requested, out):
    from .image_recipe import descriptor, LABEL
    recipe = descriptor(task)
    image = requested or "optimbench-task:" + task.name + "-" + recipe["recipe_sha256"][:20]
    try:
        return image_id(image)
    except subprocess.CalledProcessError:
        if requested:
            raise
    from . import public_packet
    if public_packet.load(task) is not None:
        public_input_preflight.ensure_public_inputs(task)
    with (out / "image-build.log").open("wb") as log:
        subprocess.run(["docker", "build", "--provenance=false", "--label", LABEL + "=" + recipe["recipe_sha256"],
                        "-t", image, str(task / "environment")],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    return image_id(image)


def assets(task, image, cache):
    """Transport trusted image assets; candidate code never runs during extraction."""
    root = cache / task.name / image.split(":")[-1]
    if (root / "ready.json").exists():
        if json.loads((root / "ready.json").read_text())["image"] != image:
            raise HarnessError("Image asset cache identity mismatch")
        return root
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root.parent, prefix="extract-") as temporary:
        stage = Path(temporary)
        cid = subprocess.check_output(["docker", "create", image], text=True).strip()
        try:
            for source, destination in [(gates.source_path(task), "pristine"),
                                        ("/app/baseline-build", "baseline"),
                                        ("/app/workload", "public-workload")]:
                subprocess.run(["docker", "cp", cid + ":" + source, str(stage / destination)], check=True)
            (stage / "projects").mkdir()
            project = subprocess.run(["docker", "cp", cid + ":/app/projects/.", str(stage / "projects")],
                                     capture_output=True)
            settings = json.loads((task / "tests/judge.json").read_text())
            needs_projects = settings.get("public_projects_required",
                task.name in ("black-project-format", "sqlfluff-project-lint",
                              "ruff-project-lint", "babel-project-compile"))
            if project.returncode and needs_projects:
                raise HarnessError("Task image is missing its public project trees")
        finally:
            subprocess.run(["docker", "rm", cid], capture_output=True, check=True)
        save(stage / "ready.json", {"image": image})
        if root.exists():
            raise HarnessError("Incomplete image cache exists; remove it before retrying")
        shutil.move(stage, root)
    return root


def upstream_gate(task, trusted, candidate, image, execution, out, cache, rebuilt_reference=None,
                  public_only=False, score_through=False):
    if rebuilt_reference is None:
        raise reference.QualificationError("Admitted rebuilt reference required for upstream gate")
    admitted = (reference.verify_for(task, trusted, image, rebuilt_reference, public_only=True)
                if public_only else reference.verify_for(task, trusted, image, rebuilt_reference))
    rules = json.loads((task / "release.json").read_text())["source_policy"]
    source_sha = policy.source_digest(policy.envelope(candidate / "source", trusted / "pristine", rules))
    base_sha = admitted["source_sha256"]
    base_build = admitted["build_sha256"]
    candidate_build = build_inventory(candidate / "build")
    baseline = admitted["upstream"]
    require_baseline(task.name, task, baseline)
    identical = source_sha == base_sha and candidate_build == base_build
    rejected = None
    try:
        actual = baseline if identical else gates.execute_suites(
            task, candidate / "source", candidate / "build", image, execution["cpuset"],
            out / "upstream/candidate", cache / "compiler-cache")
    except ValueError as error:
        # The admitted reference passed this same runner, so a ValueError (including
        # UpstreamRejected) is the candidate's outcome; harness faults raise HarnessError.
        if not score_through:
            raise
        actual, rejected = getattr(error, "receipts", None), str(error)
    if rejected is None:
        try:
            upstream.compare(baseline, actual, task.name)
        except ValueError as error:
            if not score_through:
                raise
            rejected = str(error)
    receipt = {"task": task.name, "image": image, "source_sha256": source_sha,
               "build_sha256": candidate_build, "baseline": baseline, "candidate": actual,
               "reference_key": admitted["key"], "reference_build_sha256": base_build,
               "identical_candidate_gate_reused": identical, "claimable": False,
               **({"rejected": rejected} if rejected else {})}
    path = out / "upstream.json"
    save(path, receipt)
    return path, source_sha


def project_assets(task, trusted, public_only):
    if public_only:
        return trusted / "projects"
    heldout.check(task / "tests", require_available=True)
    commitment = heldout.sha(task / "tests/heldout.lock.json")
    target = trusted / ("heldout-projects-" + commitment)
    if target.exists():
        return target
    with tempfile.TemporaryDirectory(dir=trusted, prefix="private-projects-") as temporary:
        projects = Path(temporary) / "projects"
        shutil.copytree(trusted / "projects", projects, symlinks=True)
        # Pinned HTTPS archives are fetched before timing, never in case containers.
        # Per-case inventory checks still verify cached project contents on every run.
        heldout.fetch_archives(task / "tests", projects)
        projects.rename(target)
    return target


def run(args, result):
    task = (ROOT / "tasks" / args.task).resolve()
    if not task.is_relative_to(ROOT / "tasks") or not (task / "release.json").is_file():
        raise ValueError("Unknown task ID")
    return run_task(task, args, result)


@heldout.acquired
def run_task(task, args, result):
    """Acquire private reference inputs before image builds and scored timing."""
    from .sampling_policy import resolve
    sampling = resolve(json.loads((task / "tests/judge.json").read_text()),
                       getattr(args, "minimum", None), getattr(args, "maximum", None),
                       getattr(args, "warmups", None), getattr(args, "half_width", None))
    result["sampling_policy"] = sampling
    rc_requested = getattr(args, "gates_only", False) or getattr(args, "gate_profile", "legacy") == "v1.1-rc"
    result["public_only"] = getattr(args, "gates_only", False) or heldout.check(task / "tests")["status"] != "available"
    mapping, metadata = artifacts(task, args.artifacts)
    image = ensure_image(task, args.image, args.out)
    result["image"] = image
    from .image_recipe import observation
    result["image_identity"] = observation(task, image)
    execution = score_host.select(image, args.cpuset)
    result["host"] = {**execution["host"], "pinning_took_effect": execution["pinning_took_effect"],
                      "requested_cores": execution["requested_cores"],
                      "available_cores": execution["available_cores"]}
    result["sandbox_isolation"] = score_host.require_separate(
        execution, args.agent_host_fingerprint or metadata.get("agent_host_fingerprint"),
        args.separate_sandbox or metadata.get("separate_sandbox") is True)
    trusted = assets(task, image, args.cache)
    result["public_input_preflight"] = public_input_preflight.ensure_public_inputs(
        task, source=trusted / "public-workload", fetch=True)
    projects = project_assets(task, trusted, result["public_only"])
    reference_options = {"execution_host": execution}
    if getattr(args, "gates_only", False):
        reference_options["public_only"] = True
    rebuilt_reference = reference.ensure(task, trusted, image, execution["cpuset"], args.cache,
                                         **reference_options)
    baseline = rebuilt_reference["build"]
    result.update(reference_key=rebuilt_reference["key"],
                  reference_build_sha256=rebuilt_reference["build_sha256"])
    score_through = getattr(args, "score_through", False) and not getattr(args, "gates_only", False)
    flags = []
    if score_through:
        result.update(score_through=True, flags=flags)

    def flag(gate, status, reasons, correctness, **evidence):
        """Score-through: record a gate outcome for review instead of stopping."""
        flags.append({"gate": gate, "status": status, "reasons": list(reasons),
                      "correctness": correctness, **evidence})

    def failed(gate, status, reason):
        """Score-through: a candidate that cannot be timed scores zero instead of an invalid run."""
        flag(gate, status, [reason], True)
        result.update(status=status, correct=False, speedup=None, optim_score=0.0,
                      score_basis="candidate failed before timing", claimable=False,
                      timing_performed=False, aa_sampling_performed=False)
        return result

    rules = json.loads((task / "release.json").read_text())["source_policy"]
    with tempfile.TemporaryDirectory(prefix="judge-submission-") as staging:
        submission = {"artifacts": mapping}
        if score_through:
            # Edits outside the editable scope are undone and flagged; admission
            # failures that remain are the candidate's own.
            submission = {"source": Path(staging) / "source"}
            repairs = policy.repair(mapping, trusted / "pristine", submission["source"], rules)
            result["source_repairs"] = repairs
            if repairs["restored"] or repairs["removed"]:
                flag("source", "out_of_scope_edits_ignored",
                     [f"Restored {len(repairs['restored'])} protected path(s) and removed "
                      f"{len(repairs['removed'])} path(s) added outside the editable scope"],
                     False, repairs=repairs)
            try:
                policy.envelope(submission["source"], trusted / "pristine", rules)
            except ValueError as error:
                return failed("source", "source_rejected", str(error))
        try:
            build.build(task, trusted / "pristine", image, execution["cpuset"], args.out / "candidate",
                        execution_host=execution, **submission)
        except build.BuildFailed as error:
            if not score_through:
                raise
            return failed("build", "build_failed", str(error))

    try:
        result["public_interface"] = interfaces.check(
            task, baseline, args.out / "candidate/build", image, execution["cpuset"])
    except interfaces.InterfaceBroken as error:
        if not score_through:
            raise
        result["public_interface"] = error.report
        flag("interface", "interface_broken", [str(error)], True)
    admission = policy.envelope(args.out / "candidate/source", trusted / "pristine", rules)
    behavioral = behavior.check(task, baseline, args.out / "candidate/build",
                                image, execution["cpuset"], args.out / "behavior",
                                trusted / "public-workload/inputs", projects)
    result["behavioral_gates"] = behavioral
    runtime_review = runtime_state.review(task, admission["runtime_configuration_review"], behavioral)
    result.update(runtime_review)
    if behavioral.get("status") and score_through:
        flag("behavior", behavioral["status"], ["Verifier behavioral gate did not pass"],
             behavioral["status"] != "needs_review",
             **({"rejection_policy": "process-state-hard-block-no-approval"}
                if behavioral["status"] == "needs_review" else {}))
    elif behavioral.get("status"):
        result.update(status=behavioral["status"],
                      correct=False if behavioral["status"] == "differential_mismatch" else None,
                      speedup=None, claimable=False, reasons=["Verifier behavioral gate did not pass"])
        if rc_requested and behavioral["status"] == "needs_review":
            result.update(correct=False, admission_rejected=True,
                          rejection_policy="process-state-hard-block-no-approval",
                          gate_profile=fuzzing_generic.PROFILE)
        return result
    if runtime_review["runtime_configuration_review"] and score_through:
        flag("runtime_configuration", "needs_review",
             ["Added process-global runtime configuration requires independent review"], False,
             rejection_policy="runtime-configuration-hard-block-no-approval",
             findings=runtime_review["runtime_configuration_review"])
    elif runtime_review["runtime_configuration_review"]:
        result.update(status="needs_review", correct=None, speedup=None, claimable=False,
                      runtime_configuration_review=runtime_review["runtime_configuration_review"],
                      reasons=["Added process-global runtime configuration requires independent review"])
        if rc_requested:
            result.update(correct=False, admission_rejected=True,
                          rejection_policy="runtime-configuration-hard-block-no-approval",
                          gate_profile=fuzzing_generic.PROFILE)
        return result
    upstream_options = {"public_only": True} if getattr(args, "gates_only", False) else {}
    receipt, source_sha = upstream_gate(task, trusted, args.out / "candidate",
                                        image, execution, args.out, args.cache, rebuilt_reference,
                                        score_through=score_through, **upstream_options)
    upstream_rejection = json.loads(Path(receipt).read_text()).get("rejected") if score_through else None
    result["upstream_gate"] = "passed"
    if upstream_rejection:
        result["upstream_gate"] = "rejected (scored through)"
        flag("upstream", "incorrect", [upstream_rejection], True)
    gates_only = getattr(args, "gates_only", False)
    if gates_only or getattr(args, "gate_profile", "legacy") == "v1.1-rc":
        generic = fuzzing_generic.check(
            task, baseline, args.out / "candidate/build", image, execution["cpuset"],
            args.out / "generic-gates", public_inputs=trusted / "public-workload/inputs",
            projects=projects, process_state=behavioral.get("process_state"),
            sources={"baseline": rebuilt_reference.get("source_sha256"), "candidate": source_sha})
        result["generic_gates"] = generic
        result["gate_profile"] = fuzzing_generic.PROFILE
        from .cpu_policy import correctness_receipt
        cpu_proof = None
        if generic["passed"] or score_through:
            from .cpu_correctness import check as check_cpu_correctness
            cpu_proof = check_cpu_correctness(
                task, baseline, args.out / "candidate/build", image,
                args.out / "cpu-correctness", public_inputs=trusted / "public-workload/inputs",
                projects=projects,
                sources={"baseline": rebuilt_reference.get("source_sha256"), "candidate": source_sha})
        cpu_gate = correctness_receipt(cpu_proof)
        result["cpu_correctness"] = cpu_proof
        result["cpu_correctness_gate"] = cpu_gate
        if score_through:
            if not generic["passed"]:
                failing = [f"{name}: {record['status']}" + (f" ({record['reason']})" if record.get("reason") else "")
                           for name, record in sorted(generic.get("gates", {}).items())
                           if record.get("status") not in ("passed", "not_applicable")]
                # Pending, invalid-run and nonrepeatable-baseline outcomes are missing
                # evidence, not candidate faults.
                flag("generic", generic["status"], failing or ["Generic RC gates did not pass"],
                     generic["status"] in ("contract_regression", "sanitizer_error", "nondeterministic"))
            if not cpu_gate["passed"]:
                flag("cpu_subsets", (cpu_proof or {}).get("status") or "missing",
                     ["Required affinity-verified 1/2/4/8 CPU correctness evidence is missing or failed"],
                     (cpu_proof or {}).get("status") == "contract_regression")
        elif generic["passed"] and not cpu_gate["passed"]:
            cpu_status = (cpu_proof or {}).get("status")
            status = ("contract_regression" if cpu_status == "contract_regression" else
                      "invalid run" if cpu_status == "invalid" else "generic_gates_pending")
            result.update(status=status, correct=False if status == "contract_regression" else None,
                          speedup=None, claimable=False,
                          timing_performed=False, aa_sampling_performed=False,
                          reasons=["Required affinity-verified 1/2/4/8 CPU correctness evidence is missing or failed"])
            return result
        if gates_only or not generic["passed"] and not score_through:
            result.update(status=generic["status"], correct=True if generic["passed"] else
                          False if generic["status"] in ("contract_regression", "needs_review") else None,
                          speedup=None, claimable=False, timing_performed=False,
                          aa_sampling_performed=False, source_sha256=source_sha)
            return result
    cpu_class = "unregistered-" + hashlib.sha256(
        (execution["host"].get("model") or "unknown").encode()).hexdigest()[:12]
    from . import host_registry
    mandatory = ["source", "interface", "behavior", "upstream", "generic", "cpu_subsets"]
    flagged = {item["gate"] for item in flags}
    gate_statuses = {"source": "flagged" if "source" in flagged else "passed",
                     **{gate: "flagged" if gate in flagged or gate == "behavior" and "runtime_configuration" in flagged
                        else "passed" for gate in ("interface", "behavior", "upstream")},
                     "generic": "passed" if result.get("generic_gates", {}).get("passed") else "pending",
                     "cpu_subsets": "passed" if result.get("cpu_correctness_gate", {}).get("passed") else "pending"}
    registration = None
    if all(value == "passed" for value in gate_statuses.values()):
        try:
            registration = host_registry.prepare_registered_session(
                task.name, ROOT / "judge/hosts/registry.json", mandatory_gates=mandatory, gates=gate_statuses,
                session_control=args.session_control)
            cpu_class = registration.cpu_class
            result["registration"] = registration.as_dict()
            result["registry_links"] = registration.as_dict()["links"]
            save(args.out / "registration-before-timing.json", result["registration"])
        except host_registry.RegistrationError as error:
            result["registration_attempt"] = {"eligible_to_measure_registered": False,
                                              "reasons": error.reasons, "links": host_registry._thaw(error.links)}
    registration_options = {"registration_context": registration} if registration is not None else {}
    measurement = evaluate(
        task, baseline, args.out / "candidate/build", projects, image,
        execution["cpuset"], cpu_class, seed=args.seed,
        minimum=sampling["effective"]["minimum_pairs"],
        maximum=sampling["effective"]["maximum_pairs"],
        half_width=sampling["effective"]["relative_ci_half_width"],
        warmups=sampling["effective"]["warmups"],
        upstream_receipt=receipt, source_sha256=source_sha, provisional=registration is None, execution_host=execution,
        public_inputs=trusted / "public-workload/inputs" if result["public_only"] else None,
        behavior_receipt=behavioral, review_findings=admission["runtime_configuration_review"],
        reference_info=rebuilt_reference, timing_root=args.out / "timing", score_through=score_through,
        **registration_options)
    measurement["sampling_policy"] = sampling
    verdict = score_result.interpret(measurement)
    measurement.update(diagnostic_only=registration is None, aa_noise_floor=verdict["aa_noise_floor"])
    save(args.out / "measurements.json", measurement)
    result.update(verdict)
    if score_through and measurement.get("incorrect_cases"):
        flag("outputs", "incorrect", ["Output contract mismatch on: " + ", ".join(measurement["incorrect_cases"])], True)
    result.update(host=measurement["host"], public_only=measurement["public_only"],
                  quality=measurement["quality"], image=image, protocol=measurement["protocol"],
                  seed=measurement["seed"], cpu_class=cpu_class,
                  source_sha256=source_sha, build_sha256=measurement["candidate_build_sha256"])
    if registration is not None:
        session = {**measurement, "task_id": task.name,
                   "scope": "public" if measurement["public_only"] else "heldout",
                   "mandatory_gates": mandatory, "gates": gate_statuses,
                   "links": registration.as_dict()["links"], "session_control": registration.session_control}
        eligibility = host_registry.evaluate_claimability(
            session, ROOT / "judge/hosts/registry.json", registration.judge_identity)
        result.update(mode="registered", provisional=False, claimability=eligibility,
                      claimable=eligibility["claimable"], registry_links=eligibility["links"])
    else:
        result.update(claimable=False, claimability={"claimable": False,
                      "reasons": result.get("registration_attempt", {}).get(
                          "reasons", ["registration_prerequisites_not_passed"])})
    return result


def finalize_flags(result):
    """Flags never stop timing, but a flagged result is never claimable and
    correctness flags make it incorrect. Applied after any later failure too."""
    flags = result.get("flags")
    if not flags:
        return
    result.update(needs_review=True, review_status="needs_review", claimable=False,
                  flagged_gates=sorted({item["gate"] for item in flags}))
    if any(item["correctness"] for item in flags):
        result["correct"] = False
    result["reasons"] = [f"Flagged for {item['gate']}: {item['status']}" for item in flags] + \
        list(result.get("reasons", []))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--mode", choices=["provisional"], default="provisional")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--gates-only", action="store_true",
                        help="Run untimed admission/generic gates only; never invoke sampling or calibration")
    parser.add_argument("--gate-profile", choices=["legacy", "v1.1-rc"], default="v1.1-rc",
                        help="RC gates are mandatory by default; explicit legacy is diagnostic-only")
    parser.add_argument("--stop-on-gate", dest="score_through", action="store_false",
                        help="Stop at the first failed gate (old behavior). By default every gate is "
                             "recorded as a review flag and timing always proceeds")
    parser.add_argument("--image", help="Existing pinned task image; otherwise build the task image automatically")
    parser.add_argument("--cache", type=Path, default=Path("/tmp/optimbench-score-cache"))
    parser.add_argument("--cpuset")
    parser.add_argument("--agent-host-fingerprint")
    parser.add_argument("--separate-sandbox", action="store_true",
                        help="Operator attests isolation when no comparable agent host fingerprint is available")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--minimum", type=int, default=None,
                        help="Explicit override; default: task minimum_pairs")
    parser.add_argument("--maximum", type=int, default=None,
                        help="Explicit override; default: task maximum_pairs")
    parser.add_argument("--half-width", type=float, default=None)
    parser.add_argument("--warmups", type=int, default=None)
    parser.add_argument("--session-control", type=Path,
                        help="This boot's pristine control result.json; qualifies an approved host configuration")
    args = parser.parse_args()
    if (args.minimum is not None and args.minimum < 5
            or args.maximum is not None and args.maximum < 5
            or args.minimum is not None and args.maximum is not None and args.maximum < args.minimum
            or args.warmups is not None and args.warmups < 1
            or args.half_width is not None and args.half_width <= 0):
        parser.error("Require warmups >= 1, minimum >= 5, maximum >= minimum and positive half-width")
    if args.out.exists():
        parser.error("--out must be a fresh directory; existing evidence is never overwritten")
    args.out = args.out.resolve()
    args.cache = args.cache.resolve()
    if args.session_control is not None:
        args.session_control = args.session_control.resolve()
    args.out.mkdir(parents=True)
    result = {"task": args.task, "mode": "provisional", "provisional": True, "claimable": False, "correct": None,
              "public_only": True, "status": "invalid run", "speedup": None,
              "host": {"model": None, "probe_status": "not available yet"}, "aa_noise_floor": {}}
    if args.gates_only:
        result.update(mode="gates-only", gate_profile=fuzzing_generic.PROFILE,
                      timing_performed=False, aa_sampling_performed=False)
    try:
        run(args, result)
    except reference.QualificationError as error:
        result.update(status="invalid run", correct=None, task_qualification="failed",
                      reasons=[str(error)])
    except interfaces.InterfaceBroken as error:
        result.update(status="interface_broken", correct=False, speedup=None, claimable=False,
                      public_interface=error.report, reasons=[str(error)])
    except CandidateOutputRejected as error:
        result.update(status="incorrect", correct=False, speedup=None, claimable=False,
                      native_output_rejection=error.report, reasons=[str(error)],
                      timing_performed=False, aa_sampling_performed=False)
        save(args.out / "native-output-rejection.json", error.report)
    except gates.UpstreamRejected as error:
        result.update(status="incorrect", correct=False, upstream_gate="rejected",
                      reasons=[str(error)])
        save(args.out / "upstream-rejection.json", error.receipts)
    except (HarnessError, ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        result.update(status="invalid run", reasons=[str(error)])
    finalize_flags(result)
    save(args.out / "result.json", result)
    text = score_result.summary(result)
    (args.out / "summary.txt").write_text(text)
    print(text)
    if result.get("flags"):
        return 1 if result["correct"] is False else 2
    return 2 if result["status"] in ("invalid run", "needs_review", "generic_gates_pending", "baseline_nonrepeatable") else 1 if result["status"] in (
        "incorrect", "interface_broken", "differential_mismatch", "contract_regression",
        "sanitizer_error", "nondeterministic") else 0


if __name__ == "__main__":
    raise SystemExit(main())
