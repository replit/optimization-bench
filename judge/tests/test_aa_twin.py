"""A/A crosses two byte-identical files exactly as A/B does (rc.4 baseline twin)."""
import math
import os
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from judge import scheduler, verifier


def constant(build_ns):
    def measure(case, build):
        return {"correct": True, "ns": build_ns[build]}
    return measure


class SchedulerTwinTests(unittest.TestCase):
    def test_twin_lane_and_one_warmup_per_distinct_build(self):
        seen = []

        def measure(case, build):
            seen.append(build)
            return {"correct": True, "ns": 10}
        result = scheduler.session(["case"], measure, maximum=5, aa_twin="baseline_twin")
        records = result["records"]
        warm = [r["build"] for r in records if r["phase"] == "warmup"]
        self.assertEqual(sorted(warm), ["baseline", "baseline_twin", "candidate"])
        aa = [(r["lane"], r["build"]) for r in records if r["arm"] == "aa"]
        self.assertEqual(set(aa), {("baseline", "baseline"), ("candidate", "baseline_twin")})
        ab = [(r["lane"], r["build"]) for r in records if r["arm"] == "ab"]
        self.assertEqual(set(ab), {("baseline", "baseline"), ("candidate", "candidate")})
        self.assertEqual(seen.count("baseline_twin"), seen.count("candidate"))
        self.assertEqual(result["aa_comparison"], "baseline vs baseline_twin")
        self.assertTrue(result["aa_passed"])

    def test_without_twin_the_previous_schedule_is_reproduced(self):
        result = scheduler.session(["case"], constant({"baseline": 10, "candidate": 10}), seed=3, maximum=5)
        self.assertEqual(len(result["records"]), 24)
        self.assertTrue(all(r["build"] == "baseline" for r in result["records"] if r["arm"] == "aa"))
        self.assertEqual(result["aa_comparison"], "baseline vs same baseline")

    def test_twin_name_must_be_distinct(self):
        for name in ("baseline", "candidate", 3):
            with self.subTest(name=name), self.assertRaises(ValueError):
                scheduler.session(["case"], constant({"baseline": 1, "candidate": 1}), aa_twin=name)

    def test_per_file_placement_bias_surfaces_in_aa_not_as_a_speedup(self):
        # A fixed 0.3% per-file effect: the baseline copy is slow, the
        # candidate and the twin are fast. With one shared A/A file this was
        # hidden and reported as a candidate win; the twin exposes it.
        rng = random.Random(7)
        fixed = {"baseline": 1.003, "candidate": 1.0, "baseline_twin": 1.0}

        def measure(case, build):
            return {"correct": True, "ns": int(1e9 * fixed[build] * math.exp(rng.gauss(0, 2e-4)))}
        twin = scheduler.session(["case"], measure, seed=1, maximum=30, aa_twin="baseline_twin")
        aa_low, aa_high = twin["cases"]["case"]["aa"]["ci95"]
        self.assertFalse(aa_low <= 1 <= aa_high)
        self.assertFalse(twin["aa_passed"])

        def shared(case, build):
            return measure(case, build if build != "baseline_twin" else "baseline")
        old = scheduler.session(["case"], shared, seed=1, maximum=30)
        self.assertTrue(old["aa_passed"])
        self.assertGreater(old["cases"]["case"]["ab"]["ci95"][0], 1)


class CopyRotationTests(unittest.TestCase):
    def test_rotation_records_balanced_copies_and_warms_every_file(self):
        calls = []

        def measure(case, build, copy):
            calls.append((build, copy))
            return {"correct": True, "ns": 10}
        result = scheduler.session(["case"], measure, seed=4, maximum=8, aa_twin="baseline_twin", copies=4)
        warm = sorted((r["build"], r["copy"]) for r in result["records"] if r["phase"] == "warmup")
        self.assertEqual(warm, sorted((b, k) for b in ("baseline", "baseline_twin", "candidate")
                                      for k in range(4)))
        timed = [r for r in result["records"] if r["phase"] == "timed"]
        self.assertTrue(all(r["copy"] in range(4) for r in timed))
        usage = result["copy_rotation"]["timed_usage"]
        # 8 rounds: candidate and twin 8 samples each (2 full blocks), baseline 16 (4 blocks).
        for k in range(4):
            self.assertEqual(usage[f"candidate:{k}"], 2)
            self.assertEqual(usage[f"baseline_twin:{k}"], 2)
            self.assertEqual(usage[f"baseline:{k}"], 4)
        self.assertEqual(result["copy_rotation"]["copies_per_build"], 4)

    def test_rotation_keeps_the_order_schedule(self):
        plain = scheduler.session(["case"], constant({"baseline": 10, "candidate": 10, "baseline_twin": 10}),
                                  seed=9, maximum=6, aa_twin="baseline_twin")
        rotated = scheduler.session(["case"], lambda c, b, k: {"correct": True, "ns": 10},
                                    seed=9, maximum=6, aa_twin="baseline_twin", copies=1)
        strip = lambda result: [{k: v for k, v in r.items() if k != "copy"} for r in result["records"]]
        self.assertEqual(strip(plain), strip(rotated))
        self.assertTrue(all(r["copy"] == 0 for r in rotated["records"]))

    def test_rotation_requires_twin_and_bounded_count(self):
        for kwargs in ({"copies": 2}, {"aa_twin": "baseline_twin", "copies": 0},
                       {"aa_twin": "baseline_twin", "copies": 17}, {"aa_twin": "baseline_twin", "copies": 2.0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                scheduler.session(["case"], lambda *_: {"correct": True, "ns": 1}, **kwargs)

    def test_fixed_per_copy_offsets_cannot_bias_aa_or_ab_under_rotation(self):
        # Every file gets a fixed placement offset of up to +/-0.3%, drawn from the
        # same population for every build. A single draw (copy 0) puts a slow file
        # on the baseline and fast ones on the twin and candidate: A/A and A/B are
        # both biased by ~0.6%. With K=4 balanced rotation each lane averages the
        # same offsets, so both intervals stay centred (up to ordinary 95% misses).
        offsets = {"baseline": [1.003, 0.998, 1.001, 0.997],
                   "baseline_twin": [0.997, 1.001, 0.998, 1.003],
                   "candidate": [0.997, 1.003, 1.001, 0.998]}
        failures = {"single": 0, "rotated": 0}
        points = []
        seeds = range(6)
        for seed in seeds:
            rng = random.Random(seed)

            def timed(build, copy):
                return {"correct": True,
                        "ns": int(1e9 * offsets[build][copy] * math.exp(rng.gauss(0, 2e-4)))}
            single = scheduler.session(["case"], lambda c, b: timed(b, 0), seed=seed, maximum=20,
                                       aa_twin="baseline_twin")
            rotated = scheduler.session(["case"], lambda c, b, k: timed(b, k), seed=seed, maximum=20,
                                        aa_twin="baseline_twin", copies=4)
            points += [math.log(rotated["cases"]["case"][arm]["speedup"]) for arm in ("aa", "ab")]
            for name, result in (("single", single), ("rotated", rotated)):
                arms = result["cases"]["case"]
                if not all(arms[arm]["ci95"][0] <= 1 <= arms[arm]["ci95"][1] for arm in ("aa", "ab")):
                    failures[name] += 1
        self.assertEqual(failures["single"], len(seeds))
        self.assertLessEqual(failures["rotated"], 1)
        self.assertLess(abs(sum(points) / len(points)), 5e-4)


class FourLaneTests(unittest.TestCase):
    LANES = ("baseline", "candidate", "baseline_a1", "baseline_a2")

    def session(self, measure, **kwargs):
        options = {"seed": 4, "maximum": 8, "copies": 4, "aa_builds": ("baseline_a1", "baseline_a2")}
        options.update(kwargs)
        return scheduler.session(["case"], measure, **options)

    def test_disjoint_lanes_run_once_per_round_and_every_copy_equally(self):
        result = self.session(lambda c, b, k: {"correct": True, "ns": 10})
        records = result["records"]
        warm = sorted((r["build"], r["copy"]) for r in records if r["phase"] == "warmup")
        self.assertEqual(warm, sorted((b, k) for b in self.LANES for k in range(4)))
        timed = [r for r in records if r["phase"] == "timed"]
        rounds = {}
        for r in timed:
            rounds.setdefault(r["round"], []).append(r["build"])
        self.assertTrue(all(sorted(builds) == sorted(self.LANES) for builds in rounds.values()))
        arms = {(r["arm"], r["lane"]): r["build"] for r in timed}
        self.assertEqual(arms, {("ab", "baseline"): "baseline", ("ab", "candidate"): "candidate",
                                ("aa", "baseline"): "baseline_a1", ("aa", "candidate"): "baseline_a2"})
        usage = result["copy_rotation"]["timed_usage"]
        self.assertEqual(set(usage.values()), {2})  # 8 rounds, K=4: every copy of every lane twice
        self.assertEqual(len(usage), 16)
        self.assertEqual(result["aa_comparison"], "baseline_a1 vs baseline_a2")
        self.assertEqual(result["lane_layout"]["aa"], ["baseline_a1", "baseline_a2"])

    def test_williams_order_balances_slots_and_first_order_carry_over(self):
        result = self.session(lambda c, b, k: {"correct": True, "ns": 10}, maximum=8, seed=13)
        timed = sorted((r for r in result["records"] if r["phase"] == "timed"), key=result["records"].index)
        for block in (range(0, 4), range(4, 8)):
            rows = [[r["build"] for r in timed if r["round"] == index] for index in block]
            for slot in range(4):
                self.assertEqual(sorted(row[slot] for row in rows), sorted(self.LANES))
            follows = [(row[i], row[i + 1]) for row in rows for i in range(3)]
            self.assertEqual(len(set(follows)), 12)  # every ordered pair of distinct lanes once

    def test_williams_rounds_shape_and_no_back_to_back_lane(self):
        for seed in range(50):
            rounds = scheduler.williams_rounds(30, random.Random(seed))
            self.assertEqual(len(rounds), 30)
            self.assertTrue(all(sorted(r) == sorted(scheduler.SLOTS) for r in rounds))
            sequence = [slot for r in rounds for slot in r]
            self.assertTrue(all(a != b for a, b in zip(sequence, sequence[1:])))

    def test_layout_validation(self):
        bad = [{"aa_builds": ("baseline_a1", "baseline_a2"), "copies": None},
               {"aa_builds": ("baseline_a1", "baseline_a2"), "aa_twin": "baseline_twin"},
               {"aa_builds": ("baseline", "baseline_a2")}, {"aa_builds": ("x", "x")},
               {"aa_builds": ["baseline_a1", "baseline_a2"]}]
        for kwargs in bad:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.session(lambda c, b, k: {"correct": True, "ns": 1}, **kwargs)

    def test_lane_specific_frequency_or_arm_effects_cannot_reach_aa_or_ab(self):
        # A same-file carry-over benefit and a "recently-run lane" benefit both
        # favoured the double-frequency baseline in rc.5. With disjoint lanes run
        # once per round they apply equally to all lanes, so pristine A/A and A/B
        # stay centred.
        misses = 0
        for seed in range(6):
            rng = random.Random(seed)
            state = {"previous": None}

            def measure(case, build, copy):
                carry = -0.003 if state["previous"] == (build, copy) else 0.0
                state["previous"] = (build, copy)
                return {"correct": True, "ns": int(1e9 * math.exp(carry + rng.gauss(0, 5e-4)))}
            result = self.session(measure, seed=seed, maximum=20)
            arms = result["cases"]["case"]
            misses += sum(not arms[a]["ci95"][0] <= 1 <= arms[a]["ci95"][1] for a in ("aa", "ab"))
        self.assertLessEqual(misses, 1)


class TimingCopyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name, payload in (("baseline", b"same"), ("candidate", b"same")):
            (self.root / name / "bin").mkdir(parents=True)
            (self.root / name / "bin/tool").write_bytes(payload)
            (self.root / name / "bin/tool").chmod(0o755)
            os.symlink("tool", self.root / name / "bin/alias")
            # Expanded duplicates of one binary (as Git's builtins become after copytree).
            for builtin in ("git-add", "git-diff"):
                (self.root / name / "bin" / builtin).write_bytes(b"binary")
                os.utime(self.root / name / "bin" / builtin, ns=(10**18, 10**18))
            (self.root / name / "bin/other").write_bytes(b"binary")  # same bytes, other mtime

    def test_four_lanes_fresh_identical_copies_made_together(self):
        expected = verifier.build_inventory(self.root / "baseline")
        builds, receipt, root = verifier.timing_builds(
            self.root / "baseline", self.root / "candidate", self.root / "out/timing", 5, expected, copies=3)
        self.assertEqual(set(builds), {"baseline", "candidate", *verifier.AA_BUILDS})
        self.assertTrue(all(len(paths) == 3 for paths in builds.values()))
        paths = [path for group in builds.values() for path in group]
        self.assertEqual({p.parent.parent for p in paths}, {self.root / "out/timing"})
        tools = {(p / "bin/tool").stat().st_ino for p in paths}
        tools |= {(self.root / n / "bin/tool").stat().st_ino for n in ("baseline", "candidate")}
        self.assertEqual(len(tools), 14)
        for path in paths:
            self.assertEqual(verifier.build_inventory(path), expected)
            self.assertTrue((path / "bin/alias").is_symlink())
            self.assertTrue(os.access(path / "bin/tool", os.X_OK))
            add, diff, other = ((path / "bin" / n).stat() for n in ("git-add", "git-diff", "other"))
            self.assertEqual(add.st_ino, diff.st_ino)  # identical bytes, mode and mtime: linked
            self.assertNotEqual(add.st_ino, other.st_ino)  # different mtime: kept separate
            self.assertEqual(add.st_mtime_ns, 10**18)
        inodes_per_lane = {name: {(p / "bin/git-add").stat().st_ino for p in group}
                           for name, group in builds.items()}
        self.assertEqual(sum(len(v) for v in inodes_per_lane.values()), 12)  # never linked across copies
        self.assertEqual(receipt["lanes"], {"ab": ["baseline", "candidate"], "aa": list(verifier.AA_BUILDS)})
        self.assertEqual(set(receipt["build_sha256"].values()), {expected})
        self.assertEqual(receipt["copies_per_build"], 3)
        self.assertTrue(receipt["synced_before_timing"])
        self.assertEqual(receipt["files_hard_linked"], 12)
        self.assertEqual(sorted(receipt["write_order"]),
                         sorted(f"{b}:{k}" for b in builds for k in range(3)))
        self.assertEqual(root, self.root / "out/timing")
        self.assertNotIn(str(self.root), str(receipt))
        _, again, _ = verifier.timing_builds(
            self.root / "baseline", self.root / "candidate", self.root / "again", 5, expected, copies=3)
        self.assertEqual(again["write_order"], receipt["write_order"])

    def test_inode_cached_inventory_matches_build_inventory(self):
        os.link(self.root / "baseline/bin/tool", self.root / "baseline/bin/tool-link")
        self.assertEqual(verifier._inventory(self.root / "baseline")[0],
                         verifier.build_inventory(self.root / "baseline"))

    def test_copy_count_setting(self):
        self.assertEqual(verifier.timing_copy_count({}), 4)
        self.assertEqual(verifier.timing_copy_count({"timing_copies_per_build": 2}), 2)
        self.assertEqual(verifier.timing_copy_count({"timing_copies_per_build": 2}, 6), 6)
        for bad in (0, 17, 2.0, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                verifier.timing_copy_count({"timing_copies_per_build": bad})

    def test_tampered_copy_is_refused(self):
        original = verifier._copy_linked

        def tamper(source, destination, keys):
            result = original(source, destination, keys)
            if Path(destination).parent.name == "candidate" and Path(destination).name == "1":
                (Path(destination) / "bin/tool").write_bytes(b"different")
            return result
        with patch.object(verifier, "_copy_linked", side_effect=tamper), \
                self.assertRaises(verifier.HarnessError):
            verifier.timing_builds(self.root / "baseline", self.root / "candidate",
                                   self.root / "tampered", 1, copies=2)

    def test_existing_root_and_wrong_reference_are_refused(self):
        (self.root / "used").mkdir()
        with self.assertRaises(verifier.HarnessError):
            verifier.timing_builds(self.root / "baseline", self.root / "candidate", self.root / "used", 1)
        with self.assertRaises(verifier.HarnessError):
            verifier.timing_builds(self.root / "baseline", self.root / "candidate",
                                   self.root / "fresh", 1, "0" * 64)


if __name__ == "__main__":
    unittest.main()
