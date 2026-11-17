"""Catch task-hook gaps that guarantee a pending/invalid generic gate before a VM judge run.

Every task hook request is evaluated with the same predicates the shared gates use
(judge.fuzzing_io.cli_reachable / header_reachable). A reachable "passed" is
necessary, not sufficient; anything else is a wasted judge run.
"""
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from judge import fuzzing_io as io
from judge import public_input_preflight
from judge.fuzzing_generic import task_runners

ROOT = Path(__file__).resolve().parents[2]
HOOKS = sorted(ROOT.glob("tasks/*/tests/fuzz/rc_hooks.py"))

# Requests that cannot be built offline in CI (staged public inputs, Docker-run
# library facts, or build inventories). Checked by the real judge only; this list
# must match exactly, so a newly offline-buildable request is checked here.
RUNTIME_ONLY = {
    key: reason for key, reason in {
        ("CLI_CASES", "zrip-decode"): "derives exclusion from Docker library facts",
        ("HEADER_CONSUMERS", "duckdb-analytical"):
            "Requires the pristine include/duckdb.h layout to select the frozen installed or legacy manifest",
        ("CLI_CASES", "serde-json-production-crate-index-ingestion"):
            "Requires observed stream/lifecycle facts from both real native builds before CLI exclusion",
        ("CLI_CASES", "tantivy-gharchive-single-segment-indexing"):
            "Requires original-built fixtures, runtime library metadata, image and CPU placement",
        ("HEADER_CONSUMERS", "tantivy-gharchive-single-segment-indexing"):
            "Requires the admitted pristine source tree for the full native header inventory",
    }.items() if (ROOT / "tasks" / key[1] / "tests/fuzz/rc_hooks.py").is_file()
}

# These requests are checked normally when originals are staged. Metadata-only
# CI may defer them only after a read-only preflight proves missing input bodies.
PUBLIC_INPUT_REQUESTS = {
    key for key in {
        ("CLI_CASES", "jq-strip-openapi-descriptions"),
        ("CLI_CASES", "bzip2-codec"),
        ("CLI_CASES", "sqlfmt-cli"),
    } if (ROOT / "tasks" / key[1] / "tests/fuzz/rc_hooks.py").is_file()
}

# Known gaps: the gate cannot pass today. Each entry asserts the gap is still
# present, so fixing it fails this test until the entry is deleted.
KNOWN_GAPS = {}

# Native binaries whose defaults read config under their install prefix, and the
# step env that makes a non-root worker skip those reads.
PREFIX_SENSITIVE = {
    "git": {"env": {"GIT_CONFIG_NOSYSTEM": "1", "GIT_ATTR_NOSYSTEM": "1"}, "home_default": True},
}
ROOT_PREFIX = re.compile(
    r"""(?:--prefix|\bprefix|\bPREFIX|-DCMAKE_INSTALL_PREFIX)=["']?(/root\b|\$\{?HOME\}?|~)""")
CLI_FORMAT = "judge-generic-cli-cases-v1"
HEADER_FORMAT = "judge-generic-header-request-v1"


def evaluate(task, attribute):
    """(request, reachability) or raises when the request needs runtime state."""
    _, hooks = task_runners(task)
    with tempfile.TemporaryDirectory() as out:
        context = {"task": task, "out": Path(out), "hooks": hooks, "builds": {},
                   "public_inputs": None, "projects": None}
        if attribute == "CLI_CASES":
            value = io.request(context, attribute, CLI_FORMAT)
            if value is None:
                return None, None
            precondition = getattr(hooks, "CLI_PRECONDITION", getattr(hooks, "PRECONDITION", None))
            return value, io.cli_reachable(value, task.name, precondition, getattr(hooks, "GATES", {}))
        value = io.request(context, attribute, HEADER_FORMAT)
        return value, None if value is None else io.header_reachable(value, task, out)


def public_inputs_unavailable(task, key):
    if key not in PUBLIC_INPUT_REQUESTS:
        return False
    try:
        public_input_preflight.ensure_public_inputs(task, fetch=False, root=ROOT)
    except public_input_preflight.PublicInputsError as error:
        return bool(error.commands)
    return False


def root_prefix(task, binary):
    build = task / "environment/workload/build.sh"
    text = build.read_text() if build.is_file() else ""
    found = ROOT_PREFIX.search(text)
    if found:
        return found.group(1)
    rule = PREFIX_SENSITIVE.get(binary)
    if rule and rule["home_default"] and not re.search(r"\bprefix=", text):
        return "$HOME (upstream default)"
    return None


def prefix_findings(task, cases):
    """Native {build} CLI steps whose binary reads root/HOME config without NOSYSTEM env."""
    findings = []
    for index, spec in enumerate(cases):
        step = spec["step"] if isinstance(spec["step"], dict) else vars(spec["step"])
        program = step["argv"][0]
        if not program.startswith("{build}/"):
            continue  # Image-provided interpreters are not built with the task prefix.
        binary = Path(program).name
        prefix = root_prefix(task, binary)
        if prefix is None:
            continue
        rule = PREFIX_SENSITIVE.get(binary)
        env = step.get("env") or {}
        if rule is None:
            findings.append(f"case {index}: {binary} built with prefix {prefix}; non-root cli_faults "
                            "worker cannot read it. Add its NOSYSTEM-style env and register it here")
            continue
        missing = sorted(k for k, v in rule["env"].items() if env.get(k) != v)
        if missing:
            findings.append(f"case {index}: {binary} built with prefix {prefix} needs step env "
                            + ", ".join(missing))
    return findings


class GenericGateReachability(unittest.TestCase):
    def test_tables_name_real_tasks(self):
        names = {path.parents[2].name for path in HOOKS}
        for _, task in [*RUNTIME_ONLY, *KNOWN_GAPS, *PUBLIC_INPUT_REQUESTS]:
            self.assertIn(task, names)
        self.assertFalse(set(RUNTIME_ONLY) & set(KNOWN_GAPS))
        self.assertFalse(PUBLIC_INPUT_REQUESTS & (set(RUNTIME_ONLY) | set(KNOWN_GAPS)))

    def test_requests_can_reach_a_passing_status(self):
        for path in HOOKS:
            task = path.parents[2]
            for attribute in ("CLI_CASES", "HEADER_CONSUMERS"):
                key = (attribute, task.name)
                with self.subTest(task=task.name, hook=attribute):
                    try:
                        value, reach = evaluate(task, attribute)
                    except (KeyError, IndexError, FileNotFoundError) as error:
                        if public_inputs_unavailable(task, key):
                            continue
                        self.assertIn(key, RUNTIME_ONLY, f"request needs runtime state ({error!r}); "
                                      "make it buildable offline or add it to RUNTIME_ONLY with a reason")
                        continue
                    self.assertNotIn(key, RUNTIME_ONLY, "request now builds offline; remove RUNTIME_ONLY entry")
                    if value is None:
                        continue  # Registration is release_preflight --require-generic-hooks' job.
                    if key in KNOWN_GAPS:
                        self.assertEqual(reach["status"], "pending", reach)
                        self.assertIn(KNOWN_GAPS[key], reach["problems"],
                                      "known gap is fixed or changed; update KNOWN_GAPS")
                        continue
                    self.assertIn(reach["status"], ("passed", "not_applicable"), reach["problems"])

    def test_native_cli_steps_survive_non_root_prefix_reads(self):
        for path in HOOKS:
            task = path.parents[2]
            with self.subTest(task=task.name):
                try:
                    value, _ = evaluate(task, "CLI_CASES")
                except (KeyError, IndexError, FileNotFoundError):
                    continue  # Same RUNTIME_ONLY scope as above.
                if value and value["applicable"]:
                    self.assertEqual(prefix_findings(task, value["cases"]), [])


class PublicInputReachabilityTests(unittest.TestCase):
    def test_known_data_dependent_requests_require_readonly_unavailability_evidence(self):
        key = ("CLI_CASES", "example")
        task = ROOT / "tasks/example"
        for error, expected in (
                (None, False),
                (public_input_preflight.PublicInputsError("missing originals", ["repair"]), True),
                (public_input_preflight.PublicInputsError("invalid metadata"), False)):
            with self.subTest(error=error), patch.dict(
                    globals(), PUBLIC_INPUT_REQUESTS={key}), patch.object(
                    public_input_preflight, "ensure_public_inputs",
                    side_effect=error, return_value={}) as check:
                self.assertEqual(public_inputs_unavailable(task, key), expected)
                check.assert_called_once_with(task, fetch=False, root=ROOT)

    def test_unknown_requests_cannot_use_missing_data_as_an_exception(self):
        with patch.dict(globals(), PUBLIC_INPUT_REQUESTS=set()), patch.object(
                public_input_preflight, "ensure_public_inputs") as check:
            self.assertFalse(public_inputs_unavailable(ROOT / "tasks/example", ("CLI_CASES", "example")))
        check.assert_not_called()


class PredicateControls(unittest.TestCase):
    """Negative controls: each rule flags the mistake that cost a judge run."""
    TASK = "demo"

    SURFACE = {"id": "demo.cli", "visibility": "public", "contract_id": "c", "precondition_id": "p"}

    def case(self, payload, **extra):
        case = {"task": self.TASK, "surface_id": "demo.cli", "contract_id": "c", "precondition_id": "p",
                "payload": payload}
        return {"case": case, "surface": self.SURFACE, "payload_class": payload, "watch_inputs": ["in"],
                "step": {"argv": ["{build}/project/git", "diff"], "env": {}}, **extra}

    def reach(self, cases, **extra):
        value = {"format": CLI_FORMAT, "applicable": True, "cases": cases, **extra}
        return io.cli_reachable(value, self.TASK, lambda case, surface: {"valid": True, "reason": "ok"})

    def test_stdout_only_cases_need_readonly_evidence(self):
        self.assertEqual(self.reach([self.case("small"), self.case("large")])["status"], "pending")
        self.assertEqual(self.reach([self.case("small"), self.case("large")],
                                    readonly_applicability_evidence={"basis": "x"})["status"], "passed")
        self.assertEqual(self.reach([self.case("small"), self.case("large", output_dirs=["o"],
                                                                     required_outputs=["o/f"])])["status"], "passed")

    def test_payload_classes_and_shape(self):
        ev = {"readonly_applicability_evidence": {"basis": "x"}}
        self.assertEqual(self.reach([self.case("small")], **ev)["status"], "pending")
        self.assertEqual(self.reach([self.case("small", faults=["healthy"]), self.case("large")], **ev)["status"],
                         "invalid_run")
        self.assertEqual(self.reach([self.case("small", output_dirs=["o"]), self.case("large")])["status"],
                         "invalid_run")
        self.assertEqual(self.reach([self.case("small"), self.case("huge")], **ev)["status"], "invalid_run")

    def test_out_of_contract_cases_earn_no_credit(self):
        value = {"format": CLI_FORMAT, "applicable": True,
                 "cases": [self.case("small"), self.case("large")],
                 "readonly_applicability_evidence": {"basis": "x"}}
        result = io.cli_reachable(value, self.TASK, lambda case, surface: {"valid": False, "reason": "no"})
        self.assertEqual(result["status"], "pending")

    def test_inapplicable_needs_evidence_and_covering_callbacks(self):
        bare = {"format": CLI_FORMAT, "applicable": False, "applicability_evidence": {}}
        self.assertEqual(io.cli_reachable(bare, self.TASK, None)["status"], "pending")
        covered = {"format": CLI_FORMAT, "applicable": False,
                   "applicability_evidence": {"covering_callbacks": ["stream_faults"]}}
        self.assertEqual(io.cli_reachable(covered, self.TASK, None)["status"], "pending")
        self.assertEqual(io.cli_reachable(covered, self.TASK, None, {"stream_faults": print})["status"],
                         "not_applicable")

    def test_root_prefix_git_without_nosystem_env_is_flagged(self):
        task = ROOT / "tasks/git-history-change-summary"
        bare = [self.case("small")]
        self.assertTrue(prefix_findings(task, bare))
        fixed = [self.case("small", step={"argv": ["{build}/project/git", "diff"],
                                          "env": {"GIT_CONFIG_NOSYSTEM": "1", "GIT_ATTR_NOSYSTEM": "1"}})]
        self.assertEqual(prefix_findings(task, fixed), [])
        unknown = [self.case("small", step={"argv": ["{build}/bin/tool"], "env": {}})]
        self.assertTrue(prefix_findings(task, unknown))


if __name__ == "__main__":
    unittest.main()
