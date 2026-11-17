"""Shared RC CLI/header execution and policy; task modules supply requests only."""
import base64
import hashlib
import json
from pathlib import Path

from . import fuzzing_contract as contract
from .cli_stderr import stderr_contract
from .output_contract import load_output_comparator, DEFAULT
from . import fuzzing_outputs as outputs
from . import fuzzing_processes as process_ownership


class BaselineNonrepeatable(ValueError):
    def __init__(self, reason, evidence=None):
        super().__init__(reason)
        self.evidence = evidence


def request(context, attribute, format):
    provider = getattr(context.get("hooks"), attribute, None)
    if not callable(provider):
        return None
    value = provider(context)
    if not isinstance(value, dict) or value.get("format") != format or type(value.get("applicable")) is not bool:
        raise ValueError("Malformed shared " + attribute + " request")
    return value


def inapplicable(value):
    proof = value.get("applicability_evidence")
    if not isinstance(proof, dict) or not proof:
        return {"status": "pending", "reason": "Applicability evidence missing"}
    return {"status": "not_applicable", "reason": value.get("reason", "Reviewed public scope"),
            "applicability_evidence": proof}


def stream_bytes(value, root):
    """Never classify a truncated head as the complete diagnostic."""
    artifact = value.get("artifact")
    if artifact:
        original = Path(artifact["path"])
        path = original.resolve()
        if (original.is_symlink() or not path.is_relative_to(Path(root).resolve())
                or artifact.get("complete") is not True):
            raise ValueError("CLI raw stream artifact escapes verifier output or is incomplete")
        data = path.read_bytes()
        if (artifact["bytes"] != value["bytes"] or artifact["sha256"] != value["sha256"]
                or len(data) > artifact["max_bytes"]):
            raise ValueError("CLI raw stream artifact commitment differs")
    elif not value.get("head_truncated"):
        data = base64.b64decode(value["head_base64"], validate=True)
    elif value.get("artifact_path"):
        path = (Path(root) / value["artifact_path"]).resolve()
        if not path.is_relative_to(Path(root).resolve()) or path.is_symlink():
            raise ValueError("CLI raw stream artifact escapes verifier output")
        data = path.read_bytes()
    else:
        raise ValueError("Complete CLI diagnostic unavailable; truncated head is insufficient")
    if len(data) != value["bytes"] or hashlib.sha256(data).hexdigest() != value["sha256"]:
        raise ValueError("CLI raw diagnostic commitment differs")
    return data


def cli_semantic(row, root, policy=None, spec=None):
    if row.get("status") != "observed":
        raise ValueError("Unqualified CLI worker: " + row.get("reason", row.get("status", "missing")))
    for key in ("input_before", "input_after", "output_after", "required_outputs"):
        if not isinstance(row.get(key), dict):
            raise ValueError("Missing CLI file effects: " + key)
    policy, spec = policy or DEFAULT, spec or {}
    stdout = ({key: row["stdout"][key] for key in ("bytes", "sha256")}
              if policy["stdout"]["kind"] == "exact-bytes" else
              outputs.output_value(stream_bytes(row["stdout"], root), policy["stdout"])
              if row["stdout_capture_available"] and row["stdout"]["bytes"] else {"kind": "empty"})
    file_outputs = list(spec.get("required_outputs", ())) + list(spec.get("output_dirs", ()))
    return {"exit_code": row["exit_code"], "signal": row["signal"],
            "stderr": outputs.error_identity(stream_bytes(row["stderr"], root), spec),
            "stdout": stdout,
            "stdout_capture_available": row["stdout_capture_available"],
            "input_after": outputs.file_tree(row["input_after"], policy["files"], root, stream_bytes, file_outputs),
            "output_after": outputs.file_tree(row["output_after"], policy["files"], root, stream_bytes),
            "required_outputs": outputs.file_tree(row["required_outputs"], policy["files"], root, stream_bytes)}


def covering_callbacks(value):
    found = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("covering_callbacks", "required_library_callbacks"):
                if not isinstance(child, list) or not all(
                        item in ("stream_faults", "repeat_in_process", "aliasing") for item in child):
                    raise ValueError("Malformed library coverage condition")
                found.update(child)
            else:
                found.update(covering_callbacks(child))
    elif isinstance(value, list):
        for child in value:
            found.update(covering_callbacks(child))
    return found


def has_file_bytes(tree):
    if not isinstance(tree, dict):
        return False
    return (tree.get("kind") == "file" and type(tree.get("bytes")) is int and tree["bytes"] > 0
            or any(has_file_bytes(value) for value in tree.values() if isinstance(value, dict)))


def content_snapshot(tree):
    """Permission-only fault setup is not evidence that the CLI wrote bytes."""
    result = {}
    def walk(node):
        if not isinstance(node, dict):
            return
        for name, value in node.items():
            if not isinstance(value, dict):
                continue
            if "kind" in value:
                result[name] = {key: value[key] for key in ("kind", "bytes", "sha256", "target")
                                if key in value}
            else:
                walk(value)
    walk(tree)
    return result


def healthy_wrote_required_output(healthy, spec):
    before = {**content_snapshot(healthy["input_before"]),
              **content_snapshot(healthy.get("output_before", {}))}
    after = content_snapshot(healthy["required_outputs"])
    watched = tuple(spec.get("watch_inputs", ())) + tuple(spec.get("watch_outputs", ()))
    for name, value in after.items():
        if value.get("kind") != "file" or not value.get("bytes", 0):
            continue
        covered = any(path == "." or name == path or name.startswith(path.rstrip("/") + "/") for path in watched)
        if covered and value != before.get(name, {"kind": "missing"}):
            return True
    return False


CLI_MANDATORY_FAULTS = frozenset(("healthy", "dev_full", "closed_reader", "closed_stdout"))
CLI_DEFAULT_FAULTS = ("healthy", "dev_full", "closed_reader", "closed_stdout", "readonly_dir")
CLI_PAYLOAD_CREDIT = {"small": "small_stdout", "large": "large_stdout"}


def cli_case_faults(spec):
    """Request-shape rules cli_gate enforces per case before any worker runs."""
    if not spec.get("watch_inputs"):
        raise ValueError("CLI input file effects must be watched")
    faults = tuple(spec.get("faults", CLI_DEFAULT_FAULTS))
    if not CLI_MANDATORY_FAULTS <= set(faults):
        raise ValueError("Mandatory stdout fault requests missing")
    # A public file-output mode needs an independently meaningful RO case.
    if spec.get("output_dirs") and "readonly_dir" not in faults:
        raise ValueError("File-output readonly fault request missing")
    return faults


def cli_credit_status(credit, value):
    """Final cli_gate status from earned credit when no finding blocked it."""
    if not {"small_stdout", "large_stdout"} <= credit:
        return "pending"
    if not ("readonly_output" in credit or value.get("readonly_applicability_evidence")):
        return "pending"
    return "passed"


def cli_reachable(value, task, precondition, registered=None):
    """Best status cli_gate can reach from a CLI_CASES request without running it.

    Uses the same predicates as cli_gate: a "passed"/"not_applicable" here is
    necessary, not sufficient (healthy runs must still meet the byte bounds),
    but "pending"/"invalid" means a judge run cannot pass this gate.
    """
    problems = []
    if not value["applicable"]:
        required = covering_callbacks(value.get("applicability_evidence"))
        missing = [name for name in sorted(required) if not callable((registered or {}).get(name))]
        if missing:
            problems.append("covering library callbacks not registered: " + ", ".join(missing))
        proof = value.get("applicability_evidence")
        if not isinstance(proof, dict) or not proof:
            problems.append("applicable=False without applicability_evidence")
        return {"status": "pending" if problems else "not_applicable", "problems": problems}
    cases = value.get("cases")
    if not isinstance(cases, list) or not cases or not callable(precondition):
        return {"status": "invalid_run", "problems": ["CLI cases and public precondition required"]}
    credit = set()
    for index, spec in enumerate(cases):
        label = f"case {index} ({spec.get('payload_class')})"
        try:
            cli_case_faults(spec)
        except ValueError as error:
            return {"status": "invalid_run", "problems": [label + ": " + str(error)]}
        admitted = contract.public_case(spec["case"], spec["surface"], precondition)
        if not admitted["valid"]:
            problems.append(label + ": out_of_contract, earns no credit")
            continue
        if spec["case"].get("task") != task:
            return {"status": "invalid_run", "problems": [label + ": CLI case belongs to a different task"]}
        if spec.get("payload_class") not in CLI_PAYLOAD_CREDIT:
            return {"status": "invalid_run", "problems": [label + ": payload_class must be small or large"]}
        credit.add(CLI_PAYLOAD_CREDIT[spec["payload_class"]])
        if spec.get("output_dirs"):
            if not spec.get("required_outputs"):
                return {"status": "invalid_run",
                        "problems": [label + ": output_dirs need required_outputs for readonly credit"]}
            credit.add("readonly_output")
    status = cli_credit_status(credit, value)
    if status == "pending":
        missing = sorted({"small_stdout", "large_stdout"} - credit)
        if missing:
            problems.append("no admitted case can earn: " + ", ".join(missing))
        if "readonly_output" not in credit and not value.get("readonly_applicability_evidence"):
            problems.append("no case declares output_dirs and request has no readonly_applicability_evidence")
    return {"status": status, "problems": problems, "reachable_credit": sorted(credit)}


def cli_gate(context):
    value = request(context, "CLI_CASES", "judge-generic-cli-cases-v1")
    if value is None:
        return {"status": "pending", "reason": "Public CLI requests not registered"}
    if not value["applicable"]:
        required = covering_callbacks(value.get("applicability_evidence"))
        records = context.setdefault("gate_results", {})
        for name in required:
            if name not in records:
                callback = getattr(context.get("hooks"), "GATES", {}).get(name)
                if callable(callback):
                    from .fuzzing_generic import _call_evidence
                    records[name] = _call_evidence(name, callback(context), context["hooks"], context)
        registered = getattr(context.get("hooks"), "GATES", {})
        missing = [name for name in sorted(required) if records.get(name, {}).get("status") != "passed"
                   or not callable(registered.get(name))]
        if missing:
            return {"status": "pending", "reason": "Covering library callbacks not passed: " + ", ".join(missing),
                    "applicability_evidence": value.get("applicability_evidence"),
                    "covering_callbacks": sorted(required)}
        return inapplicable(value)
    from tools.fuzz_runners.cli_faults import run_pair
    from runner import Step
    policy = load_output_comparator(context["task"])
    cases = value.get("cases")
    precondition = getattr(context["hooks"], "CLI_PRECONDITION",
                           getattr(context["hooks"], "PRECONDITION", None))
    if not isinstance(cases, list) or not cases or not callable(precondition):
        raise ValueError("CLI cases and public precondition required")
    if context.get("cpu_limits") and len(cases) > context["cpu_limits"]["cases_per_gate"]:
        raise ValueError("CPU correctness case bound exceeded; refusing to sample")
    evidence, findings, credit, limitations = [], [], set(), []
    for index, spec in enumerate(cases):
        case, surface = spec["case"], spec["surface"]
        admitted = contract.public_case(case, surface, precondition)
        if not admitted["valid"]:
            evidence.append({"status": "out_of_contract", "candidate_verdict": None, **admitted})
            continue
        if case["task"] != Path(context["task"]).name:
            raise ValueError("CLI case belongs to a different task")
        step = Step(**spec["step"]) if isinstance(spec["step"], dict) else spec["step"]
        faults = cli_case_faults(spec)
        runs, semantics = [], []
        for replay in range(2):
            output = Path(context["out"]) / "cli-faults" / f"case-{index}" / f"replay-{replay}"
            raw = run_pair(step=step, inputs=spec["inputs"], baseline=context["baseline"],
                candidate=context["candidate"], image=context["image"], out=output,
                watch_inputs=spec["watch_inputs"], watch_outputs=spec.get("watch_outputs", ()),
                output_dirs=spec.get("output_dirs", ()), required_outputs=spec.get("required_outputs", ()),
                faults=faults, case_id=contract.case_digest(case),
                payload_class=spec["payload_class"], cpuset=context["cpuset"],
                task=context["task"], public_inputs=context["public_inputs"], projects=context["projects"],
                baseline_build_sha256=context["builds"]["baseline"],
                candidate_build_sha256=context["builds"]["candidate"])
            semantic = {}
            for fault, pair in raw["observations"].items():
                if fault == "readonly_dir" and not spec.get("output_dirs"):
                    continue  # Not an applicability claim about other CLI modes.
                semantic[fault] = {}
                for arm, row in pair.items():
                    try:
                        semantic[fault][arm] = cli_semantic(row, output, policy, spec)
                    except outputs.OutputContractError as error:
                        if arm == "baseline":
                            raise BaselineNonrepeatable("Pristine output violates declared comparator: " + str(error),
                                {"output_comparator": policy, "raw_output": str(output)}) from error
                        semantic[fault][arm] = {"declared_output_contract_violation": True}
                if not contract.typed_equal(pair["baseline"]["input_before"], pair["candidate"]["input_before"]):
                    raise ValueError("CLI workers did not start with identical inputs")
                if fault != "healthy" and pair["baseline"]["exit_code"] == 0:
                    limitations.append({"case_sha256": contract.case_digest(case), "fault": fault,
                                        "kind": "baseline_silent_output_failure",
                                        "claim": "Differential compatibility, not IO correctness"})
            healthy = raw["observations"]["healthy"]["baseline"]
            if healthy["status"] != "observed" or healthy["exit_code"] != 0:
                raise ValueError("CLI IO fixture does not succeed on pristine")
            size = healthy["stdout"]["bytes"]
            if spec["payload_class"] == "small" and 0 < size <= 4096:
                credit.add("small_stdout")
            if spec["payload_class"] == "large" and size >= 65536:
                credit.add("large_stdout")
            if spec.get("output_dirs"):
                if not spec.get("required_outputs") or not has_file_bytes(healthy["required_outputs"]):
                    raise ValueError("Readonly fixture needs actual healthy file output")
                if not healthy_wrote_required_output(healthy, spec):
                    raise ValueError("Readonly fixture needs a proved healthy content write, not setup permissions")
                readonly = raw["observations"]["readonly_dir"]["baseline"]
                if (readonly["exit_code"] == healthy["exit_code"]
                        and contract.typed_equal(content_snapshot(readonly["required_outputs"]),
                                                 content_snapshot(healthy["required_outputs"]))
                        and contract.typed_equal(content_snapshot(readonly["input_after"]),
                                                 content_snapshot(healthy["input_after"]))):
                    raise ValueError("Readonly fixture did not exercise a file-output difference")
                credit.add("readonly_output")
            runs.append(raw)
            semantics.append(semantic)
        for fault in semantics[0]:
            process_pairs = [run["observations"][fault] for run in runs]
            sources = context.get("sources") or {}
            same_pristine = bool(sources.get("baseline") and sources.get("baseline") == sources.get("candidate"))
            process_result = process_ownership.classify_pairs(process_pairs, identical_pristine=same_pristine)
            if process_result["status"] == "baseline_nonrepeatable":
                raise BaselineNonrepeatable(process_result["reason"],
                    {"output_comparator": policy, "process_ownership": process_result, "current_runs": runs})
            if process_result["status"] == "needs_review":
                findings.append({"kind": "needs_review", "fault": fault, "process_ownership": process_result})
            if not outputs.equal_cli(semantics[0][fault]["baseline"], semantics[1][fault]["baseline"], policy):
                raise BaselineNonrepeatable("Pristine CLI behavior is not repeatable under its declared comparator",
                    {"output_comparator": policy, "cases": evidence, "current_runs": runs})
            if not outputs.equal_cli(semantics[0][fault]["candidate"], semantics[1][fault]["candidate"], policy):
                findings.append({"kind": "nondeterministic", "fault": fault})
            elif not outputs.equal_cli(semantics[0][fault]["baseline"], semantics[0][fault]["candidate"], policy):
                findings.append({"kind": "contract_regression", "fault": fault, "reproduced": True})
        evidence.append({"case_sha256": contract.case_digest(case), "runs": runs,
                         "comparison_spec": {key: list(map(str, spec.get(key, ())))
                                             for key in ("required_outputs", "output_dirs", "watch_inputs",
                                                         "watch_outputs")}})
    status = next((kind for kind in ("contract_regression", "nondeterministic", "needs_review")
                   if any(f["kind"] == kind for f in findings)), None)
    if status is None:
        status = cli_credit_status(credit, value)
    return {"status": status, "evidence": {"cases": evidence, "findings": findings,
            "exercised": sorted(credit), "baseline_limitations": limitations,
            "output_comparator": policy, "stderr_contract": "cli-stderr-contract-v1+os-error-identity-v1",
            "readonly_applicability_evidence": value.get("readonly_applicability_evidence")}}


def header_manifest(value, task, out):
    manifest = Path(value["manifest"]).resolve()
    if not (manifest.is_relative_to((Path(task) / "tests/fuzz").resolve()) or
            manifest.is_relative_to(Path(out).resolve())):
        raise ValueError("Header manifest must be verifier-owned")
    return manifest


def header_scope_unresolved(declaration):
    """Declared scope gaps that force header_gate to pending even when every cell passes."""
    cxx = declaration.get("cxx_scope", {})
    return (bool(declaration.get("uncovered_headers")) or bool(cxx.get("unmapped_contexts"))
            or cxx.get("status") in ("pending", "unresolved", "unmeasured",
                                     "partially_established", "incomplete"))


def header_reachable(value, task, out):
    """Best status header_gate can reach from a HEADER_CONSUMERS request without compiling."""
    if not value["applicable"]:
        proof = value.get("applicability_evidence")
        if not isinstance(proof, dict) or not proof:
            return {"status": "pending", "problems": ["applicable=False without applicability_evidence"]}
        return {"status": "not_applicable", "problems": []}
    try:
        manifest = header_manifest(value, task, out)
        declaration = json.loads(manifest.read_text())
    except (ValueError, KeyError, OSError) as error:
        return {"status": "invalid_run", "problems": [type(error).__name__ + ": " + str(error)]}
    if not declaration.get("cells"):
        return {"status": "pending", "problems": ["header manifest declares no cells"]}
    if header_scope_unresolved(declaration):
        return {"status": "pending", "problems": ["header manifest declares unresolved scope"]}
    return {"status": "passed", "problems": []}


def header_gate(context):
    value = request(context, "HEADER_CONSUMERS", "judge-generic-header-request-v1")
    if value is None:
        return {"status": "pending", "reason": "Public header consumer scope not registered"}
    if not value["applicable"]:
        return inapplicable(value)
    if not context.get("sources"):
        return {"status": "pending", "reason": "Admitted source identities missing"}
    from tools.fuzz_runners.header_run import run_pair
    manifest = header_manifest(value, context["task"], context["out"])
    declaration = json.loads(manifest.read_text())
    if context.get("cpu_limits"):
        if len(declaration["cells"]) > context["cpu_limits"]["cases_per_gate"]:
            raise ValueError("CPU correctness case bound exceeded; refusing to sample")
    unresolved_scope = header_scope_unresolved(declaration)
    runs = [run_pair(image_ref=context["image"], parent_image=context["image"],
            baseline=context["baseline"], candidate=context["candidate"], manifest=manifest,
            out=Path(context["out"]) / "header-consumers" / f"replay-{replay}", cpuset=context["cpuset"],
            baseline_source_sha256=context["sources"]["baseline"],
            candidate_source_sha256=context["sources"]["candidate"],
            baseline_build_sha256=context["builds"]["baseline"],
            candidate_build_sha256=context["builds"]["candidate"]) for replay in range(2)]
    facts = [cell["attribution"] for cell in runs[0]["cells"]]
    all_facts = facts + [cell["attribution"] for cell in runs[1]["cells"]]
    if any(f in ("invalid_run", "baseline_supported_cell_failed") for f in all_facts):
        status = "invalid_run"
    elif facts != [cell["attribution"] for cell in runs[1]["cells"]]:
        status = "nondeterministic"
    elif "candidate_compatibility_difference" in facts:
        status = "contract_regression"
    elif unresolved_scope:
        status = "pending"
    elif facts and all(f == "missing_third_party_sdk" for f in facts):
        status = "not_applicable"
    elif facts and all(f in ("same_success", "missing_third_party_sdk") for f in facts):
        status = "passed"
    else:
        status = "pending"  # Unsupported/pending cells are not passing credit.
    result = {"status": status, "evidence": {"runs": runs, "unresolved_scope": unresolved_scope,
            "scope_exclusions": [cell for cell in runs[0]["cells"]
                                if cell["attribution"] == "missing_third_party_sdk"]}}
    if status == "not_applicable":
        result["applicability_evidence"] = {"basis": "Pristine compiler proved missing optional third-party SDK",
                                            "runs": runs}
    return result
