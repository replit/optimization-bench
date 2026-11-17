"""Parallel work is allowed; CPU cost and post-operation worker leaks are explicit."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from judge import process_state, runtime_state, scheduler, score_result, statistics
from judge.verifier import resources


class CpuPolicyTests(unittest.TestCase):
    def test_thread_configuration_is_not_a_static_rejection(self):
        for name, source in {
            "x.py": "import torch, numba, threading\ntorch.set_num_threads(8)\nnumba.set_num_threads(8)\nthreading.stack_size(65536)\n",
            "x.go": 'package x\nimport "runtime"\nfunc f(){runtime.GOMAXPROCS(8);runtime.LockOSThread();runtime.UnlockOSThread()}\n',
            "x.c": "void f(void){omp_set_num_threads(8);openblas_set_num_threads(8);mkl_set_num_threads(8);}\n",
        }.items():
            self.assertEqual(runtime_state.findings({}, {name: source}), [])
        self.assertTrue(runtime_state.findings({}, {"x.py": "import gc\ngc.disable()\n"}))
        self.assertTrue(runtime_state.findings({}, {"x.c": "void f(){setenv(\"X\",\"1\",1);}"}))

    def test_joined_workers_allowed_but_post_call_leaks_fail(self):
        for right, passed in (({"threads": 1, "python_threads": 1}, True),
                              ({"threads": 2, "python_threads": 2}, False),
                              ({"threads": 1, "python_threads": 1, "rule_d_leftovers": 1}, False)):
            with self.subTest(right=right), patch.object(process_state, "probe", side_effect=[
                    {"threads": 1, "python_threads": 1}, right]):
                result = process_state.check("task", "baseline", "candidate", "image", "0-7")
            self.assertEqual(result["passed"], passed)
        with patch.object(process_state, "probe", side_effect=[{"threads": 4}, {"threads": 1}]):
            self.assertTrue(process_state.check("task", "b", "c", "image", "0-7")["passed"])

    def test_operation_cpu_is_delta_not_container_lifetime_and_sums_steps(self):
        steps = [SimpleNamespace(telemetry={"operation_cpu_time_usec": n,
                                          "cpu.stat": "usage_usec 99999\n", "memory.peak": "100"})
                 for n in (100, 200)]
        observed = resources(steps)
        self.assertEqual(observed["cpu_time_usec"], 300)
        self.assertEqual(observed["container_lifetime_cpu_time_usec"], 99999)
        steps[0].telemetry.pop("operation_cpu_time_usec")
        self.assertIsNone(resources(steps)["cpu_time_usec"])

    def test_cpu_ratio_direction_pairing_and_ci_are_reproducible(self):
        pairs = {"a": [(100, 50)] * 5, "b": [(100, 200)] * 5}
        value = statistics.cpu_ratio(pairs)
        self.assertAlmostEqual(value["ratio"], 1)
        self.assertEqual(value, statistics.cpu_ratio(pairs))
        varied = {"x": [(100, 50), (100, 200), (100, 300), (100, 50), (100, 90)]}
        value = statistics.cpu_ratio(varied)
        self.assertLess(value["ci95"][0], value["ratio"])
        self.assertGreater(value["ci95"][1], value["ratio"])
        for bad in ({}, {"x": [(0, 1)]}, {"x": [(1, -1)]}, {"x": [(float("nan"), 1)]},
                    {"x": [(1, float("inf"))]}):
            with self.assertRaises(ValueError):
                statistics.cpu_ratio(bad)

    def test_session_reports_efficiency_and_parallelism_without_changing_wall_score(self):
        for candidate_cpu, classification in ((50, "efficiency win"), (200, "parallelism win"),
                                               (100, "efficiency win")):
            def measure(case, build):
                return {"correct": True, "ns": 100 if build == "baseline" else 50,
                        "resources": {"cpu_time_usec": 100 if build == "baseline" else candidate_cpu}}
            result = scheduler.session(["case"], measure, maximum=5)
            self.assertAlmostEqual(result["speedup"], 2)
            self.assertAlmostEqual(result["cpu_ratio"], candidate_cpu / 100)
            self.assertEqual(result["cpu_classification"], classification)
            self.assertEqual(result["cpu_ratio_ci95"], [candidate_cpu / 100] * 2)
            self.assertEqual(result["cases"]["case"]["cpu_ab"]["total_usec"]["candidate"], candidate_cpu * 5)
            self.assertEqual(result["cases"]["case"]["cpu_aa"]["ratio"], 1)
            verdict = score_result.interpret({**result, "quality": {"noisy": False}})
            self.assertEqual(verdict["cpu_classification"], classification)
            report = score_result.summary({**verdict, "task": "example"})
            self.assertIn("candidate/baseline", report)
            self.assertIn("baseline 500 us", report)
            self.assertEqual(score_result.interpret(
                {**result, "quality": {"noisy": True}})["cpu_classification"], "inconclusive")

    def test_correctness_only_cases_do_not_change_cpu_pairs_or_wall_schedule(self):
        def measure(case, build):
            return {"correct": True, "ns": 100 if build == "baseline" else 50,
                    "resources": {"cpu_time_usec": 100 if build == "baseline" else 200}}
        performance = scheduler.session(["perf"], measure, maximum=5)
        with_correctness = scheduler.session(["correct", "perf"], measure, maximum=5,
                                            roles={"correct": "correctness"})
        self.assertEqual(performance["cases"], with_correctness["cases"])
        self.assertEqual(performance["cpu_ratio"], with_correctness["cpu_ratio"])
        self.assertNotIn("correct", with_correctness["cases"])
        self.assertEqual([record for record in with_correctness["records"]
                          if record["phase"] != "correctness"], performance["records"])
