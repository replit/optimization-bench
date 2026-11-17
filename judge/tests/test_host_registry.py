"""Schema examples are test fixtures only; the production registry stays empty."""
import copy
from dataclasses import FrozenInstanceError
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from jsonschema import Draft202012Validator

from judge import host_registry as hr


def fingerprint(machine="c4-standard-32", architecture="x86_64"):
    return {
        "provider": "gcp", "project_id": "test-project", "zone": "us-central1-a",
        "instance_id": "1234567890123456789", "machine_type": machine,
        "architecture": architecture, "threads_per_core": 1,
        "cpu_model": "Fixture CPU", "cpu_flags_sha256": hr._sha(b"a\nb"),
        "kernel": "6.8.0-fixture", "docker_version": "27.5.1",
        "containerd_version": "containerd 1.7.24",
        "judge_cpus": list(range(8)), "system_cpus": list(range(8, 32)),
    }


def unit():
    return {"ci95": [.995, 1.005], "sample_count": 10, "full_width_pp": 1.0}



def affinity(judge, manager):
    """sched_getaffinity fake: pid 0 is the judge process, pid 1 the systemd manager."""
    def fake(pid):
        value = judge if pid == 0 else manager
        if value is None:
            raise OSError("unavailable")
        return set(value)
    return fake

class FingerprintTests(unittest.TestCase):
    def collect(self, metadata=True, threads="1", flags="b a a"):
        commands = {
            ("lscpu", "--json"): json.dumps({"lscpu": [
                {"field": "Model name:", "data": "  Fixture   CPU  "},
                {"field": "Flags:", "data": flags},
                {"field": "Thread(s) per core:", "data": threads}]}),
            ("uname", "-m"): "x86_64", ("uname", "-r"): "6.8.0-fixture",
            ("docker", "version", "--format", "{{.Server.Version}}"): "27.5.1",
            ("containerd", "--version"): "containerd 1.7.24",
        }
        values = {
            "project/project-id": "test-project",
            "instance/zone": "projects/123/zones/us-central1-a",
            "instance/id": "1234567890123456789",
            "instance/machine-type": "projects/123/machineTypes/c4-standard-32",
        }
        with patch.object(hr, "_command", side_effect=lambda *args: commands.get(args)), \
                patch.object(hr, "_metadata", side_effect=lambda n: values[n] if metadata else None), \
                patch.object(hr.os, "sched_getaffinity", side_effect=affinity(set(range(8)), set(range(8, 32)))), \
                patch.object(hr, "_text", return_value="8-31"):
            return hr.collect_fingerprint()

    def test_complete_live_observations_validate(self):
        value = self.collect()
        self.assertEqual(value, fingerprint())
        schema = json.loads((hr.SCHEMAS / "registry.schema.json").read_text())
        Draft202012Validator({"$defs": schema["$defs"],
                             "$ref": "#/$defs/fingerprint"}).validate(value)

    def test_flags_are_deduplicated_sorted_without_newline(self):
        self.assertEqual(self.collect(flags="b a b")["cpu_flags_sha256"],
                         hashlib.sha256(b"a\nb").hexdigest())

    def test_missing_metadata_is_explicitly_unknown(self):
        value = self.collect(metadata=False)
        for key in ("provider", "project_id", "zone", "instance_id", "machine_type"):
            self.assertIsNone(value[key])

    def test_smt_is_observed_not_falsified(self):
        self.assertEqual(self.collect(threads="2")["threads_per_core"], 2)

    def test_missing_flags_are_not_hash_of_empty_string(self):
        self.assertIsNone(self.collect(flags="")["cpu_flags_sha256"])

    def test_all_observations_unavailable(self):
        with patch.object(hr, "_command", return_value=None), \
                patch.object(hr, "_metadata", return_value=None), \
                patch.object(hr, "_text", return_value=None), \
                patch.object(hr.os, "sched_getaffinity", side_effect=OSError):
            value = hr.collect_fingerprint()
        self.assertTrue(all(v is None for v in value.values()))
        self.assertEqual(set(value), set(fingerprint()))

    def test_system_set_is_not_inferred_from_nominal_machine_size(self):
        with patch.object(hr, "_command", return_value=None), \
                patch.object(hr, "_metadata", return_value=None), \
                patch.object(hr, "_text", return_value=None), \
                patch.object(hr.os, "sched_getaffinity", side_effect=affinity({2, 4}, None)):
            value = hr.collect_fingerprint()
        self.assertEqual(value["judge_cpus"], [2, 4])
        self.assertIsNone(value["system_cpus"])

    def system_cpus(self, slice_text, manager):
        with patch.object(hr, "_command", return_value=None), \
                patch.object(hr, "_metadata", return_value=None), \
                patch.object(hr, "_text", return_value=slice_text), \
                patch.object(hr.os, "sched_getaffinity", side_effect=affinity(set(range(8)), manager)):
            return hr.collect_fingerprint()["system_cpus"]

    def test_system_set_follows_manager_affinity_when_cgroup_spans_all(self):
        # provision-host.sh: systemd CPUAffinity=8-31, system.slice cpuset left at all CPUs.
        self.assertEqual(self.system_cpus("0-31", set(range(8, 32))), list(range(8, 32)))

    def test_system_set_follows_cgroup_when_manager_is_unrestricted(self):
        self.assertEqual(self.system_cpus("8-31", set(range(32))), list(range(8, 32)))

    def test_system_set_is_the_intersection_of_both_observations(self):
        self.assertEqual(self.system_cpus("4-31", set(range(16))), list(range(4, 16)))

    def test_system_set_uses_the_observable_source(self):
        self.assertEqual(self.system_cpus(None, set(range(8, 32))), list(range(8, 32)))
        self.assertEqual(self.system_cpus("8-31", None), list(range(8, 32)))

    def test_metadata_requires_google_response_header(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {}
        response.read.return_value = b"not-google"
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(hr.urllib.request, "build_opener", return_value=opener):
            self.assertIsNone(hr._metadata("project/project-id"))
            response.headers = {"Metadata-Flavor": "Google"}
            self.assertEqual(hr._metadata("project/project-id"), "not-google")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Metadata-flavor"), "Google")


class EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="registry-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.addCleanup(patch.stopall)
        patch.object(hr, "ROOT", self.root).start()
        self.live = fingerprint()
        patch.object(hr, "collect_fingerprint", side_effect=lambda: copy.deepcopy(self.live)).start()
        patch.dict(os.environ, {key: value for key, value in os.environ.items()
                                if not key.startswith("DOCKER_")}, clear=True).start()
        self.docker_endpoint = "unix:///var/run/docker.sock"
        self.docker_context = "default"
        self.docker_selected = {
            "ID": "fixture-daemon-id", "Name": "fixture-host", "ServerVersion": "27.5.1",
            "OSType": "linux", "Architecture": "x86_64", "KernelVersion": "6.8.0-fixture",
        }
        self.docker_local = copy.deepcopy(self.docker_selected)
        self.local_hostname = "fixture-host"
        original_command = hr._command

        def command(*args):
            if args[:3] == ("docker", "context", "show"):
                return self.docker_context
            if args[:3] == ("docker", "context", "inspect"):
                return json.dumps({"Host": self.docker_endpoint})
            if args[:2] == ("docker", "info"):
                return json.dumps(self.docker_selected)
            if args[:3] == ("docker", "--host", "unix:///var/run/docker.sock"):
                return json.dumps(self.docker_local)
            if args == ("uname", "-n"):
                return self.local_hostname
            if args == ("uname", "-s"):
                return "Linux"
            return original_command(*args)

        patch.object(hr, "_command", side_effect=command).start()
        self.execution = hr.collect_execution_host(self.live)
        for name, value in {
            "judge/engine.py": "production = True\n",
            "tools/runner/runner.py": "runner = True\n",
            "tools/qualify_aa.py": "qualification = True\n",
            "tasks/test-task/task.json": "{}",
            "tasks/test-task/release.json": "{}",
            "tasks/test-task/tests/judge.json": "{}",
            "tasks/test-task/environment/workload/task.py": "driver = True\n",
            "judge/tests/example.py": "# not production\n",
        }.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
        for name in ("registry.schema.json", "qualification.schema.json"):
            path = self.root / "judge/hosts" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(hr.SCHEMAS / name, path)
        self.git("init", "-q")
        self.git("config", "user.name", "Registry Test")
        self.git("config", "user.email", "registry-test@example.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "Fixture release")
        self.git("tag", "optimbench-v1.0.2")
        self.identity = hr.collect_judge_identity("test-task")
        self.assertTrue(self.identity["clean"])
        self.evidence_name = "judge/hosts/qualifications/aa.json"
        evidence = self.root / self.evidence_name
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_bytes(b'{"original_observations":[1,2,3]}\n')
        self.qualification = {
            "schema_version": 1, "host_id": "gcp:test-project:us-central1-a:1234567890123456789",
            "task_id": "test-task", "scope": "heldout",
            "fingerprint_sha256": hr.canonical_sha256(self.live),
            "judge_code_sha256": self.identity["judge_code_sha256"],
            "task_policy_sha256": self.identity["task_policy_sha256"],
            "units": {"work": unit()}, "quality": {"noisy": False},
            "noise_counters": {"before": {"steal": 1}, "after": {"steal": 1}},
            "evidence": {"path": self.evidence_name, "sha256": hr._sha(evidence.read_bytes())},
        }
        self.qualification_name = "judge/hosts/qualifications/receipt.json"
        self.host = {
            "host_id": self.qualification["host_id"], "role": "scoring",
            "fingerprint": copy.deepcopy(self.live),
            "review": {"pull_request": "https://github.com/example/benchmark/pull/1"},
            "qualifications": {},
        }
        self.registry = {"schema_version": 1, "hosts": [self.host]}
        self.registry_path = self.root / "judge/hosts/registry.json"
        self.session = {
            "task_id": "test-task", "scope": "heldout", "correct": True,
            "aa_passed": True, "quality": {"noisy": False},
            "cases": {"work": {"aa": {
                "ci95": [.995, 1.005], "sample_count": 10, "ci95_full_width_pp": 1.0,
                "status": "resolved"}}},
            "mandatory_gates": ["upstream", "differential"],
            "gates": {"upstream": "passed", "differential": "passed"},
        }
        self.save_qualification()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root,
                                       stderr=subprocess.DEVNULL, text=True).strip()

    def save_registry(self):
        self.registry_path.write_text(json.dumps(self.registry, indent=2) + "\n")
        self.session["links"] = {
            "registry_sha256": hr._sha(self.registry_path.read_bytes()),
            "entry_sha256": hr.canonical_sha256(self.host),
            "fingerprint_sha256": hr.canonical_sha256(self.live),
            "qualification_sha256": self.host["qualifications"]["test-task"]["sha256"],
            "evidence_sha256": self.qualification["evidence"]["sha256"],
            "judge_commit": self.identity["commit"], "release_tag": self.identity["release_tag"],
            "judge_code_sha256": self.identity["judge_code_sha256"],
            "task_policy_sha256": self.identity["task_policy_sha256"],
            "execution_host_sha256": hr.canonical_sha256(self.execution),
        }

    def save_qualification(self):
        path = self.root / self.qualification_name
        raw = json.dumps(self.qualification).encode() + b"\n"
        path.write_bytes(gzip.compress(raw) if path.suffix == ".gz" else raw)
        self.host["qualifications"]["test-task"] = {
            "path": self.qualification_name, "sha256": hr._sha(path.read_bytes())}
        self.save_registry()

    def evaluate(self):
        return hr.evaluate_claimability(self.session, self.registry_path, self.identity)

    def prepare(self):
        return hr.prepare_registered_session(
            "test-task", self.registry_path, mandatory_gates=self.session["mandatory_gates"],
            gates=self.session["gates"])

    def preflight_denied(self, reason):
        with self.assertRaises(hr.RegistrationError) as raised:
            self.prepare()
        self.assertIn(reason, raised.exception.reasons)
        return raised.exception

    def denied(self, reason):
        result = self.evaluate()
        self.assertIs(result["claimable"], False, result)
        self.assertIn(reason, result["reasons"])

    def test_all_good_is_claimable_with_verified_links(self):
        result = self.evaluate()
        self.assertEqual(result, {"claimable": True, "reasons": [],
                                  "links": self.session["links"]})

    def test_exact_host_receipt_is_used_even_when_a_configuration_matches(self):
        self.registry["configurations"] = [{
            "configuration_id": "fixture-config", "role": "scoring",
            "fingerprint": {k: self.live[k] for k in hr.CONFIGURATION_FIELDS},
            "review": self.host["review"]}]
        self.save_registry()
        context = self.prepare()
        self.assertEqual(context.host_id, self.host["host_id"])
        self.assertIsNone(context.session_control)
        self.assertNotIn("boot_id", context.links)
        self.assertEqual(self.evaluate(), {"claimable": True, "reasons": [],
                                           "links": self.session["links"]})

    def test_preflight_success_without_current_session_aa(self):
        del self.session["aa_passed"], self.session["cases"], self.session["quality"]
        context = self.prepare()
        self.assertIsInstance(context, hr.RegistrationContext)
        self.assertEqual(context.host_id, self.host["host_id"])
        self.assertEqual(context.registry_entry_sha256, hr.canonical_sha256(self.host))
        self.assertEqual(context.as_dict()["live_fingerprint"], self.live)
        self.assertEqual(context.judge_tag, self.identity["release_tag"])
        self.assertEqual(context.judge_commit, self.identity["commit"])
        self.assertEqual(context.judge_code_sha256, self.identity["judge_code_sha256"])
        self.assertEqual(context.task_policy_sha256, self.identity["task_policy_sha256"])
        self.assertEqual(context.qualification_receipt_sha256,
                         self.host["qualifications"]["test-task"]["sha256"])
        self.assertEqual(context.qualified_units, ("work",))
        self.assertEqual(context.scope, "heldout")
        self.assertEqual(dict(context.links), self.session["links"])
        self.assertTrue(context.cpu_class.startswith("registered:c4-standard-32:"))
        self.assertFalse(self.evaluate()["claimable"])

    def test_context_is_deeply_frozen_and_receipt_copy_is_independent(self):
        context = self.prepare()
        with self.assertRaises(FrozenInstanceError):
            context.host_id = "override"
        for mapping in (context.live_fingerprint, context.links,
                        context.execution_host, context.judge_identity, context.gates):
            with self.assertRaises(TypeError):
                mapping["override"] = True
        self.assertIsInstance(context.live_fingerprint["judge_cpus"], tuple)
        copy_for_receipt = context.as_dict()
        copy_for_receipt["live_fingerprint"]["judge_cpus"].append(999)
        self.assertEqual(context.live_fingerprint["judge_cpus"], tuple(range(8)))
        json.dumps(copy_for_receipt)

    def test_cpu_class_is_stable_across_registry_order_and_evidence_updates(self):
        first = self.prepare()
        self.qualification["noise_counters"]["after"]["new_counter"] = 5
        self.save_qualification()
        self.registry_path.write_text(json.dumps(self.registry, sort_keys=True))
        second = self.prepare()
        self.assertEqual(first.cpu_class, second.cpu_class)
        self.assertNotEqual(first.registry_entry_sha256, second.registry_entry_sha256)

    def test_cpu_class_distinguishes_registered_hosts(self):
        first = self.prepare()
        self.host["host_id"] += ":other"
        self.qualification["host_id"] = self.host["host_id"]
        self.save_qualification()
        self.assertNotEqual(first.cpu_class, self.prepare().cpu_class)

    def test_preflight_does_not_grant_claimability_without_session_aa(self):
        context = self.prepare()
        self.session["links"] = context.as_dict()["links"]
        del self.session["aa_passed"]
        self.denied("session_quality_unclean")

    def test_off_centre_session_aa_outside_the_band_is_denied(self):
        self.session["links"] = self.prepare().as_dict()["links"]
        self.session["aa_passed"] = False
        self.session["cases"]["work"]["aa"].update(ci95=[1.030, 1.040], ci95_full_width_pp=1.0)
        self.denied("session_quality_unclean")

    def test_small_aa_offset_inside_the_band_is_claimable(self):
        # Real-world case: a unit's 95% A/A CI [1.0020, 1.0247] excludes 1 only by
        # layout-sized offsets; it lies inside the ±2.5% equivalence band.
        self.session["links"] = self.prepare().as_dict()["links"]
        self.session["aa_passed"] = False
        self.session["cases"]["work"]["aa"].update(ci95=[1.0020, 1.0247], ci95_full_width_pp=2.27)
        result = self.evaluate()
        self.assertTrue(result["claimable"], result)

    def test_postflight_accepts_frozen_context_judge_identity(self):
        context = self.prepare()
        result = hr.evaluate_claimability(self.session, self.registry_path, context.judge_identity)
        self.assertTrue(result["claimable"], result)

    def test_reference_requested_cpuset_cannot_prove_affinity(self):
        self.session["execution_host"] = {"requested_cores": list(range(8)),
                                         "pinning_took_effect": True,
                                         "effective_cores": list(range(8))}
        self.live["judge_cpus"] = list(range(8, 16))
        self.preflight_denied("fingerprint_unregistered")
        self.denied("fingerprint_unregistered")

    def test_affinity_change_after_preflight_is_denied(self):
        context = self.prepare()
        self.session["links"] = context.as_dict()["links"]
        self.live["judge_cpus"] = list(range(16))
        self.denied("fingerprint_unregistered")

    def test_docker_tcp_ssh_and_http_endpoints_are_denied_pre_and_post(self):
        for endpoint in ("tcp://other:2375", "tcp://127.0.0.1:2375",
                         "ssh://other", "http://other", "npipe:////./pipe/docker_engine"):
            with self.subTest(endpoint=endpoint), patch.dict(os.environ, {"DOCKER_HOST": endpoint}):
                self.preflight_denied("docker_endpoint_nonlocal")
                self.denied("docker_endpoint_nonlocal")

    def test_remote_docker_context_is_denied(self):
        self.docker_context = "remote"
        for endpoint in ("tcp://other:2375", "ssh://other"):
            self.docker_endpoint = endpoint
            with patch.dict(os.environ, {"DOCKER_CONTEXT": "remote"}):
                self.preflight_denied("docker_endpoint_nonlocal")
                self.denied("docker_endpoint_nonlocal")

    def test_local_context_cannot_hide_remote_docker_host(self):
        with patch.dict(os.environ, {"DOCKER_CONTEXT": "default",
                                     "DOCKER_HOST": "ssh://other"}):
            self.preflight_denied("docker_endpoint_nonlocal")
            self.denied("docker_endpoint_nonlocal")

    def test_local_endpoint_environment_is_bound(self):
        with patch.dict(os.environ, {"DOCKER_HOST": "unix:///run/docker.sock"}):
            context = self.prepare()
            self.assertEqual(context.execution_host["endpoint"], "unix:///run/docker.sock")

    def test_explicit_context_precedence_is_bound(self):
        with patch.dict(os.environ, {"DOCKER_CONTEXT": "default",
                                     "DOCKER_HOST": "unix:///ignored.sock"}):
            context = self.prepare()
            self.assertEqual(context.execution_host["endpoint"], self.docker_endpoint)

    def test_missing_docker_context_is_unknown_not_assumed_local(self):
        self.docker_context = None
        self.preflight_denied("docker_endpoint_unknown")
        self.denied("docker_endpoint_unknown")

    def test_malformed_context_endpoint_is_unknown(self):
        self.docker_endpoint = None
        self.preflight_denied("docker_endpoint_unknown")

    def test_missing_server_identity_is_denied(self):
        for key in tuple(self.docker_selected):
            with self.subTest(field=key):
                original = self.docker_selected.pop(key)
                self.preflight_denied("docker_server_identity_unknown")
                self.denied("docker_server_identity_unknown")
                self.docker_selected[key] = original

    def test_unknown_local_hostname_is_denied(self):
        self.local_hostname = None
        self.preflight_denied("docker_server_identity_unknown")

    def test_independent_local_daemon_must_be_observable(self):
        self.docker_local = None
        self.preflight_denied("docker_server_identity_unknown")
        self.denied("docker_server_identity_unknown")

    def test_preflight_does_not_start_or_time_any_container(self):
        self.prepare()
        commands = [call.args for call in hr._command.call_args_list]
        docker_commands = [command for command in commands if command[0] == "docker"]
        self.assertTrue(docker_commands)
        self.assertTrue(all(command[1] in ("context", "info", "--host")
                            for command in docker_commands))

    def test_selected_server_must_match_independently_local_daemon(self):
        for field, reason in {
            "ID": "docker_server_id_mismatch",
            "Name": "docker_server_hostname_mismatch",
            "ServerVersion": "docker_server_version_mismatch",
            "OSType": "docker_server_os_mismatch",
            "Architecture": "docker_server_architecture_mismatch",
            "KernelVersion": "docker_server_kernel_mismatch",
        }.items():
            with self.subTest(field=field):
                original = self.docker_selected[field]
                self.docker_selected[field] = "different"
                self.preflight_denied(reason)
                self.denied(reason)
                self.docker_selected[field] = original

    def test_even_both_daemons_must_match_local_fingerprinted_host(self):
        for field, reason in {
            "Name": "docker_server_hostname_mismatch",
            "ServerVersion": "docker_server_version_mismatch",
            "OSType": "docker_server_os_mismatch",
            "Architecture": "docker_server_architecture_mismatch",
            "KernelVersion": "docker_server_kernel_mismatch",
        }.items():
            with self.subTest(field=field):
                original = self.docker_selected[field]
                self.docker_selected[field] = self.docker_local[field] = "different"
                self.preflight_denied(reason)
                self.docker_selected[field] = self.docker_local[field] = original

    def test_daemon_architecture_alias_is_normalized(self):
        self.docker_selected["Architecture"] = self.docker_local["Architecture"] = "amd64"
        self.assertEqual(self.prepare().execution_host["server_architecture"], "x86_64")

    def test_daemon_change_after_preflight_invalidates_link(self):
        context = self.prepare()
        self.session["links"] = context.as_dict()["links"]
        self.docker_selected["ID"] = self.docker_local["ID"] = "new-local-daemon"
        self.denied("session_links_mismatch")

    def test_example_intel_and_portability_entries_validate(self):
        for machine, architecture, role in (
                ("c4-standard-32", "x86_64", "scoring"),
                ("c4a-standard-32", "aarch64", "portability")):
            example = copy.deepcopy(self.host)
            example["role"] = role
            example["fingerprint"] = fingerprint(machine, architecture)
            hr._validate({"schema_version": 1, "hosts": [example]}, "registry.schema.json")
        hr._validate(self.qualification, "qualification.schema.json")

    def test_scoring_is_intel_only(self):
        from jsonschema import ValidationError
        example = copy.deepcopy(self.host)
        example["fingerprint"] = fingerprint("c3d-standard-30", "x86_64")
        with self.assertRaises(ValidationError):
            hr._validate({"schema_version": 1, "hosts": [example]}, "registry.schema.json")

    def test_production_registry_is_the_reference_intel_configuration(self):
        actual = json.loads((hr.SCHEMAS / "registry.json").read_text())
        hr._validate(actual, "registry.schema.json")
        # No individual machines are registered: any fresh VM matching the reference
        # configuration scores through it with its own same-boot pristine control.
        self.assertEqual(actual["hosts"], [])
        self.assertEqual(len(actual["configurations"]), 1)
        configuration = actual["configurations"][0]
        self.assertEqual(configuration["role"], "scoring")
        self.assertEqual(configuration["fingerprint"], {
            "architecture": "x86_64",
            "cpu_model": "INTEL(R) XEON(R) PLATINUM 8581C CPU @ 2.30GHz",
            "judge_cpus": list(range(8)), "machine_type": "c4-standard-32", "provider": "gcp",
            "system_cpus": list(range(8, 16)), "threads_per_core": 1})

    def test_every_fingerprint_field_must_match(self):
        original = copy.deepcopy(self.live)
        for key in original:
            with self.subTest(field=key):
                self.live = copy.deepcopy(original)
                self.live[key] = None
                self.preflight_denied("fingerprint_unregistered")
                self.denied("fingerprint_unregistered")

    def test_untagged_checkout_is_not_claimable(self):
        self.git("tag", "-d", "optimbench-v1.0.2")
        self.preflight_denied("judge_untagged")
        self.denied("judge_untagged")

    def test_tag_on_other_commit_is_not_a_release(self):
        self.git("commit", "--allow-empty", "-qm", "Untagged")
        self.identity = hr.collect_judge_identity("test-task")
        self.denied("judge_untagged")

    def test_arbitrary_tag_is_not_a_release(self):
        self.git("tag", "-d", "optimbench-v1.0.2")
        self.git("tag", "local-approval")
        self.denied("judge_untagged")

    def test_noisy_session_is_denied(self):
        self.session["quality"]["noisy"] = True
        self.denied("session_quality_unclean")

    def test_missing_quality_is_not_clean(self):
        del self.session["quality"]
        self.denied("session_quality_unclean")

    def test_missing_noise_counters_is_not_clean(self):
        self.session["quality"]["missing_cpu_counters"] = True
        self.denied("session_quality_unclean")

    def test_pending_failed_unknown_and_missing_gate_are_denied(self):
        for status in ("pending", "failed", "skipped", None, True):
            with self.subTest(status=status):
                self.session["gates"]["upstream"] = status
                self.preflight_denied("gate_not_passed")
                self.denied("gate_not_passed")
        del self.session["gates"]["upstream"]
        self.denied("gate_not_passed")

    def test_no_gate_inventory_is_not_a_vacuous_pass(self):
        self.session["mandatory_gates"] = []
        self.session["gates"] = {}
        self.denied("gate_not_passed")

    def test_additional_pending_gate_also_blocks(self):
        self.session["gates"]["additional"] = "pending"
        self.denied("gate_not_passed")

    def test_tampered_qualification_bytes(self):
        with (self.root / self.qualification_name).open("ab") as output:
            output.write(b" ")
        self.preflight_denied("qualification_hash_mismatch")
        self.denied("qualification_hash_mismatch")

    def test_tampered_original_evidence_bytes(self):
        with (self.root / self.evidence_name).open("ab") as output:
            output.write(b" ")
        self.denied("qualification_hash_mismatch")

    def test_gzip_receipts_hash_compressed_file_bytes(self):
        self.qualification_name += ".gz"
        self.save_qualification()
        self.assertTrue(self.evaluate()["claimable"])
        with (self.root / self.qualification_name).open("ab") as output:
            output.write(b" ")
        self.denied("qualification_hash_mismatch")

    def test_session_must_link_every_verified_identity(self):
        for key in self.session["links"]:
            with self.subTest(link=key):
                saved = self.session["links"][key]
                self.session["links"][key] = "tampered"
                self.denied("session_links_mismatch")
                self.session["links"][key] = saved

    def test_qualification_binds_task_host_scope_code_policy_and_fingerprint(self):
        for key in ("task_id", "host_id", "scope", "judge_code_sha256",
                    "task_policy_sha256", "fingerprint_sha256"):
            with self.subTest(field=key):
                saved = self.qualification[key]
                self.qualification[key] = ("public" if key == "scope" else
                                           "0" * 64 if key.endswith("sha256") else "other-task")
                self.save_qualification()
                if key != "scope":
                    self.preflight_denied("qualification_identity_mismatch")
                self.denied("qualification_identity_mismatch")
                self.qualification[key] = saved
        self.save_qualification()

    def test_noisy_qualification_cannot_be_approved(self):
        self.qualification["quality"]["noisy"] = True
        self.save_qualification()
        self.denied("qualification_invalid")

    def test_qualification_ci_must_be_centred(self):
        # Off-centre beyond the ±2.5% equivalence band (statistics-policy.md).
        self.qualification["units"]["work"]["ci95"] = [1.03, 1.04]
        self.save_qualification()
        self.preflight_denied("qualification_quality_unclean")
        self.denied("qualification_quality_unclean")

    def test_qualification_width_is_recomputed_not_trusted(self):
        self.qualification["units"]["work"]["ci95"] = [.9, 1.1]
        self.save_qualification()
        self.denied("qualification_quality_unclean")

    def test_session_ci_width_count_and_resolution_are_checked(self):
        aa = self.session["cases"]["work"]["aa"]
        for field, value in (("ci95", [1.03, 1.04]), ("sample_count", 4),
                             ("ci95_full_width_pp", .1), ("status", "not resolved"),
                             ("ci95", [float("nan"), 1.0])):
            with self.subTest(field=field):
                original = aa[field]
                aa[field] = value
                self.denied("session_quality_unclean")
                aa[field] = original

    def test_session_must_have_exact_qualified_units(self):
        self.session["cases"]["extra"] = copy.deepcopy(self.session["cases"]["work"])
        self.denied("qualification_quality_unclean")

    def test_portability_host_never_claims_performance(self):
        self.live["architecture"] = "aarch64"
        self.live["machine_type"] = "c4a-standard-32"
        self.host["fingerprint"] = copy.deepcopy(self.live)
        self.host["role"] = "portability"
        self.qualification["fingerprint_sha256"] = hr.canonical_sha256(self.live)
        self.save_qualification()
        self.preflight_denied("portability_only")
        self.denied("portability_only")

    def test_unregistered_host_is_denied(self):
        self.registry["hosts"] = []
        self.registry_path.write_text(json.dumps(self.registry))
        self.denied("fingerprint_unregistered")

    def test_missing_task_qualification_is_denied(self):
        self.session["task_id"] = "other-task"
        self.denied("qualification_missing")

    def test_preflight_missing_task_qualification_is_denied(self):
        self.host["qualifications"]["other-task"] = self.host["qualifications"].pop("test-task")
        self.registry_path.write_text(json.dumps(self.registry))
        self.preflight_denied("qualification_missing")

    def test_ambiguous_live_fingerprint_is_denied(self):
        second = copy.deepcopy(self.host)
        second["host_id"] += ":second"
        self.registry["hosts"].append(second)
        self.save_registry()
        self.preflight_denied("fingerprint_ambiguous")
        self.denied("fingerprint_ambiguous")

    def test_unknown_judge_identity_is_denied(self):
        with patch.object(hr, "collect_judge_identity", side_effect=ValueError("missing identity")):
            self.preflight_denied("judge_identity_unknown")
            self.denied("judge_identity_unknown")

    def test_duplicate_entries_are_denied(self):
        self.registry["hosts"].append(copy.deepcopy(self.host))
        self.save_registry()
        self.denied("registry_invalid")

    def test_invalid_registry_cannot_be_used(self):
        self.host["review"]["pull_request"] = "approved"
        self.save_registry()
        self.preflight_denied("registry_invalid")
        self.denied("registry_invalid")

    def test_registry_dictionary_is_not_an_override(self):
        result = hr.evaluate_claimability(self.session, self.registry, self.identity)
        self.assertFalse(result["claimable"])
        self.assertIn("registry_invalid", result["reasons"])

    def test_force_claimable_inputs_cannot_override_failure(self):
        self.session.update(force_claimable=True, claimable=True, override=True)
        self.session["quality"]["noisy"] = True
        self.denied("session_quality_unclean")

    def test_receipt_traversal_and_symlinks_are_denied(self):
        original = self.qualification_name
        self.host["qualifications"]["test-task"]["path"] = (
            "judge/hosts/qualifications/../receipt.json")
        self.save_registry()
        self.denied("qualification_invalid")
        self.host["qualifications"]["test-task"]["path"] = original
        path = self.root / original
        raw = path.read_bytes()
        path.unlink()
        outside = self.root / "outside.json"
        outside.write_bytes(raw)
        path.symlink_to(outside)
        self.save_registry()
        self.denied("qualification_invalid")

    def test_forged_judge_identity_is_not_trusted(self):
        self.identity["commit"] = "0" * 40
        self.identity["clean"] = True
        self.denied("judge_identity_mismatch")

    def test_dirty_production_is_denied(self):
        (self.root / "judge/engine.py").write_text("changed = True\n")
        self.preflight_denied("judge_dirty")
        self.denied("judge_dirty")

    def test_dirty_task_driver_is_denied(self):
        (self.root / "tasks/test-task/environment/workload/task.py").write_text("changed\n")
        self.denied("judge_dirty")

    def test_deleted_optional_driver_is_dirty(self):
        driver = self.root / "tasks/test-task/environment/workload/runner.py"
        driver.write_text("driver\n")
        self.git("add", str(driver))
        self.git("commit", "-qm", "Add driver")
        driver.unlink()
        self.assertFalse(hr.collect_judge_identity("test-task")["clean"])

    def test_git_observation_failure_is_not_clean(self):
        original = hr._command

        def command(*args):
            return None if args[:2] == ("git", "diff") else original(*args)

        with patch.object(hr, "_command", side_effect=command):
            self.assertFalse(hr.collect_judge_identity("test-task")["clean"])

    def test_data_and_tests_do_not_change_production_code_identity(self):
        before = hr.judge_code_sha256()
        (self.root / "judge/tests/example.py").write_text("changed test\n")
        (self.root / self.evidence_name).write_text("changed evidence\n")
        self.registry_path.write_text("changed registry\n")
        (self.root / "judge/hosts/qualifications/new.json").write_text("{}")
        (self.root / "tools/tests").mkdir()
        (self.root / "tools/tests/new.py").write_text("test\n")
        self.assertEqual(before, hr.judge_code_sha256())
        self.assertTrue(hr.collect_judge_identity("test-task")["clean"])

    def test_production_runner_and_pinned_schemas_change_code_identity(self):
        for name in ("judge/engine.py", "tools/runner/runner.py",
                     "tools/qualify_aa.py", "judge/hosts/registry.schema.json"):
            with self.subTest(path=name):
                before = hr.judge_code_sha256()
                path = self.root / name
                path.write_bytes(path.read_bytes() + b"\n")
                self.assertNotEqual(before, hr.judge_code_sha256())

    def test_task_policy_hash_changes_when_configuration_changes(self):
        before = hr.task_policy_sha256("test-task")
        (self.root / "tasks/test-task/tests/judge.json").write_text('{"gate":"new"}')
        self.assertNotEqual(before, hr.task_policy_sha256("test-task"))

    def test_canonical_identity_uses_ascii_and_no_trailing_newline(self):
        value = {"z": "\u00e9", "a": [1, 2]}
        self.assertEqual(hr.canonical_sha256(value),
                         hashlib.sha256(b'{"a":[1,2],"z":"\\u00e9"}').hexdigest())

    def test_malformed_inputs_deny_instead_of_claiming(self):
        for session, identity in ((None, self.identity), (self.session, None), ({}, {})):
            result = hr.evaluate_claimability(session, self.registry_path, identity)
            self.assertFalse(result["claimable"])
            self.assertTrue(result["reasons"])

    def test_invalid_task_ids_are_fail_closed(self):
        for task_id in (None, [], {}, "../../other", "", 12):
            with self.subTest(task_id=task_id):
                self.session["task_id"] = task_id
                self.denied("invalid_task_id")

    def test_duplicate_json_keys_are_rejected(self):
        raw = self.registry_path.read_text()
        self.registry_path.write_text(raw.replace('"schema_version": 1',
                                                 '"schema_version": 1, "schema_version": 1'))
        self.denied("registry_invalid")

    def test_missing_schema_dependency_is_explicit(self):
        with patch.object(hr, "_validate", side_effect=ImportError("jsonschema")):
            self.preflight_denied("schema_validator_unavailable")
            self.denied("schema_validator_unavailable")

    def test_missing_qualification_receipt_denies(self):
        (self.root / self.qualification_name).unlink()
        self.preflight_denied("qualification_invalid")
        self.denied("qualification_invalid")

    def test_preflight_invalid_task_ids_are_denied(self):
        for task_id in (None, [], {}, "../../other", "", 12):
            with self.subTest(task_id=task_id), self.assertRaises(hr.RegistrationError) as raised:
                hr.prepare_registered_session(
                    task_id, self.registry_path, mandatory_gates=["upstream"],
                    gates={"upstream": "passed"})
            self.assertIn("invalid_task_id", raised.exception.reasons)

    def test_compressed_original_evidence_hash_is_of_file_bytes(self):
        path = self.root / self.evidence_name
        compressed = path.with_suffix(".json.gz")
        compressed.write_bytes(gzip.compress(path.read_bytes()))
        self.qualification["evidence"] = {
            "path": compressed.relative_to(self.root).as_posix(),
            "sha256": hr._sha(compressed.read_bytes())}
        self.save_qualification()
        self.assertTrue(self.evaluate()["claimable"])
        compressed.write_bytes(compressed.read_bytes() + b" ")
        self.denied("qualification_hash_mismatch")


class ConfigurationTests(unittest.TestCase):
    """A fresh VM of an approved configuration, qualified by this boot's pristine control."""
    TASK = "git-history-change-summary"
    setUp_base = EligibilityTests.setUp
    git = EligibilityTests.git
    save_qualification = EligibilityTests.save_qualification
    save_registry = EligibilityTests.save_registry

    def setUp(self):
        from tools.tests.test_judge_control import TASK_UNITS, pristine, result_for
        self.pristine, self.result_for = pristine, result_for
        self.setUp_base()
        for name in ("task.json", "release.json", "tests/judge.json", "environment/workload/task.py"):
            path = self.root / "tasks" / self.TASK / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n")
        self.git("add", ".")
        self.git("commit", "-qm", "Fixture task")
        self.git("tag", "-f", "optimbench-v1.0.2")
        self.identity = hr.collect_judge_identity(self.TASK)
        self.assertTrue(self.identity["clean"])
        # A new VM: instance, zone, project and CPU flags differ from the exact host.
        self.live.update(instance_id="999", zone="us-east1-b", project_id="other-project",
                         cpu_flags_sha256=hr._sha(b"other\nflags"))
        self.configuration = {
            "configuration_id": "gcp-c4-standard-32-fixture", "role": "scoring",
            "fingerprint": {k: self.live[k] for k in hr.CONFIGURATION_FIELDS},
            "review": self.host["review"]}
        self.registry["configurations"] = [self.configuration]
        self.registry_path.write_text(json.dumps(self.registry, indent=2) + "\n")
        self.boot = "boot-1"
        patch.object(hr, "_boot_id", side_effect=lambda: self.boot).start()
        self.units = TASK_UNITS[self.TASK]
        self.control_dir = self.root / "results" / (self.TASK + "--control")
        self.control_dir.mkdir(parents=True)
        self.control_path = self.control_dir / "result.json"
        self.measurement = self.pristine(self.units)
        self.control = {**self.result_for(self.measurement), "task": self.TASK,
                        "registration_attempt": {"reasons": ["qualification_missing"], "links": {
                            "boot_id": self.boot, "fingerprint_sha256": hr.canonical_sha256(self.live),
                            "entry_sha256": hr.canonical_sha256(self.configuration),
                            "judge_code_sha256": self.identity["judge_code_sha256"],
                            "task_policy_sha256": self.identity["task_policy_sha256"]}}}
        self.save_control()
        self.session = {
            "task_id": self.TASK, "scope": "heldout", "correct": True, "aa_passed": True,
            "quality": {"noisy": False},
            "cases": {name: {"aa": {"ci95": [.995, 1.005], "sample_count": 10,
                                    "ci95_full_width_pp": 1.0, "status": "resolved"}}
                      for name in self.units},
            "mandatory_gates": ["upstream"], "gates": {"upstream": "passed"},
            "session_control": str(self.control_path)}

    def save_control(self):
        self.control_path.write_text(json.dumps(self.control))
        (self.control_dir / "measurements.json").write_text(json.dumps(self.measurement))

    def prepare(self, control=True):
        return hr.prepare_registered_session(
            self.TASK, self.registry_path, mandatory_gates=self.session["mandatory_gates"],
            gates=self.session["gates"], session_control=self.control_path if control else None)

    def denied(self, reason, control=True):
        with self.assertRaises(hr.RegistrationError) as raised:
            self.prepare(control)
        self.assertIn(reason, raised.exception.reasons)
        result = hr.evaluate_claimability(
            {**self.session, **({} if control else {"session_control": None})},
            self.registry_path, self.identity)
        self.assertIs(result["claimable"], False)
        self.assertIn(reason, result["reasons"])

    def test_new_vm_with_passing_same_boot_control_is_claimable(self):
        context = self.prepare()
        self.assertEqual(context.host_id, "configuration:gcp-c4-standard-32-fixture")
        self.assertTrue(context.cpu_class.startswith("registered:c4-standard-32:"))
        self.assertEqual((context.scope, context.qualified_units), ("heldout", tuple(sorted(self.units))))
        self.assertEqual(context.session_control, str(self.control_path))
        self.assertEqual(context.links["boot_id"], self.boot)
        self.assertEqual(context.links["session_control_sha256"], hr._sha(self.control_path.read_bytes()))
        # The verifier's pre-timing re-check reproduces the same context.
        again = hr.prepare_registered_session(
            self.TASK, self.registry_path, mandatory_gates=context.mandatory_gates,
            gates=dict(context.gates), session_control=context.session_control)
        self.assertEqual(again.as_dict(), context.as_dict())
        self.session["links"] = context.as_dict()["links"]
        result = hr.evaluate_claimability(self.session, self.registry_path, context.judge_identity)
        self.assertEqual(result, {"claimable": True, "reasons": [], "links": self.session["links"]})
        # cpu_class is shared by every VM of the configuration.
        self.live["instance_id"] = "1000"
        self.control["registration_attempt"]["links"]["fingerprint_sha256"] = hr.canonical_sha256(self.live)
        self.save_control()
        self.assertEqual(self.prepare().cpu_class, context.cpu_class)

    def test_without_session_control_qualification_is_missing(self):
        self.denied("qualification_missing", control=False)

    def test_wrong_machine_type_or_cpu_model_is_unregistered(self):
        for field, value in (("machine_type", "c4-standard-16"), ("cpu_model", "Other CPU"),
                             ("system_cpus", list(range(8, 12)))):
            with self.subTest(field=field):
                original = self.live[field]
                self.live[field] = value
                self.denied("fingerprint_unregistered")
                self.live[field] = original

    def test_failed_control_is_denied(self):
        for change in ({"status": "speedup"}, {"correct": False}, {"public_only": True}):
            with self.subTest(change=change):
                saved = copy.deepcopy(self.control)
                self.control.update(change)
                self.save_control()
                self.denied("session_control_failed")
                self.control = saved
        self.measurement["cases"]["patch-histogram"]["aa"]["ci95"] = [1.01, 1.02]
        self.save_control()
        self.denied("session_control_failed")

    def test_control_from_other_boot_task_or_judge_code_is_denied(self):
        links = self.control["registration_attempt"]["links"]
        for key, value in (("boot_id", "boot-0"), ("judge_code_sha256", "0" * 64),
                           ("task_policy_sha256", "0" * 64), ("fingerprint_sha256", "0" * 64),
                           ("entry_sha256", "0" * 64)):
            with self.subTest(key=key):
                original = links[key]
                links[key] = value
                self.save_control()
                self.denied("session_control_identity_mismatch")
                links[key] = original
        self.control["task"] = "sqlglot-mozilla-transpile"
        self.save_control()
        self.denied("session_control_identity_mismatch")

    def test_missing_or_malformed_control_is_invalid(self):
        self.control_path.write_text("not json")
        self.denied("session_control_invalid")
        self.control_path.unlink()
        self.denied("session_control_invalid")

    def test_control_changed_after_preparation_is_denied(self):
        context = self.prepare()
        self.session["links"] = context.as_dict()["links"]
        self.control["point_speedup"] = 1.02
        self.save_control()
        result = hr.evaluate_claimability(self.session, self.registry_path, context.judge_identity)
        self.assertIn("session_links_mismatch", result["reasons"])

    def test_tagged_clean_release_and_gates_are_still_required(self):
        self.session["gates"] = {"upstream": "pending"}
        self.denied("gate_not_passed")
        self.session["gates"] = {"upstream": "passed"}
        self.git("tag", "-d", "optimbench-v1.0.2")
        self.denied("judge_untagged")

    def test_session_aa_and_quality_are_still_required(self):
        self.session["links"] = self.prepare().as_dict()["links"]
        self.session["quality"] = {"noisy": True}
        result = hr.evaluate_claimability(self.session, self.registry_path, self.identity)
        self.assertIn("session_quality_unclean", result["reasons"])


class SummaryLabelTests(unittest.TestCase):
    def test_claimable_result_is_labelled_qualified_otherwise_provisional_with_reasons(self):
        from judge.score_result import summary
        base = {"task": "t", "status": "inconclusive", "speedup": None, "public_only": False}
        text = summary({**base, "claimable": True, "cpu_class": "registered:c4-standard-32:abc"})
        self.assertTrue(text.startswith("QUALIFIED — CLAIMABLE\n"))
        self.assertIn("Registered scoring host: registered:c4-standard-32:abc", text)
        self.assertNotIn("unregistered", text)
        text = summary({**base, "claimable": False,
                        "claimability": {"claimable": False, "reasons": ["qualification_missing"]}})
        self.assertTrue(text.startswith("PROVISIONAL — NOT CLAIMABLE\n"))
        self.assertIn("Not claimable: qualification_missing", text)


if __name__ == "__main__":
    unittest.main()
