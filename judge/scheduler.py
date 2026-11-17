"""Randomized multiple interleaved trials and an independent in-session A/A arm."""
import math
import random
import statistics

from .statistics import aggregate_wall, cpu_ratio, geometric_mean, stopping

# Family-wise false-alarm rate for the per-unit A/A bias decision (rc.6).
AA_BIAS_FAMILY_ALPHA = .05


def bootstrap_distribution(pairs, seed, draws=5000):
    """The sorted draws statistics.bootstrap takes its 95% CI from (same RNG use)."""
    logs = [math.log(a / b) for a, b in pairs]
    rng = random.Random(seed)
    return sorted(math.exp(statistics.mean(rng.choices(logs, k=len(logs)))) for _ in range(draws))


def aa_bias_test(state, family_size, seed, draws=5000):
    """Bonferroni A/A bias test across a task's performance units.

    Unit i is biased only if its two-sided percentile-bootstrap test rejects
    "A/A ratio = 1" at alpha = 0.05 / family_size, i.e. if the
    (1 - 0.05 / family_size) interval excludes 1. The interval is read from
    the same bootstrap distribution as the unit's 95% CI (same pairs, seed and
    draws as statistics.bootstrap), so the 95% CI, the A/A precision bar and
    the A/A noise envelope are unchanged. With one unit it is the 95% CI.
    Only the bias decision uses this interval.
    """
    if type(family_size) is not int or family_size < 1:
        raise ValueError("A/A bias family size must be a positive integer")
    alpha = AA_BIAS_FAMILY_ALPHA / family_size
    if family_size == 1:
        interval = list(state["ci95"])
    else:
        distribution = bootstrap_distribution(state["pairs"], seed, draws)
        interval = [distribution[int(draws * alpha / 2)], distribution[int(draws * (1 - alpha / 2))]]
    return {"method": "bonferroni", "family_alpha": AA_BIAS_FAMILY_ALPHA, "family_size": family_size,
            "per_unit_alpha": alpha, "interval_level": 1 - alpha, "interval": interval,
            "biased": not interval[0] <= 1 <= interval[1]}


def orders(rounds, rng):
    """Randomize within balanced two-pair blocks; odd tails differ by at most one."""
    result = []
    for _ in range(rounds // 2):
        block = [("baseline", "candidate"), ("candidate", "baseline")]
        rng.shuffle(block)
        result.extend(block)
    if rounds % 2:
        result.append(tuple(rng.sample(["baseline", "candidate"], 2)))
    return result


# Williams design for 4 treatments: within a block of 4 rows every treatment takes
# every slot once and immediately follows every other treatment exactly once.
WILLIAMS4 = ((0, 1, 3, 2), (1, 2, 0, 3), (2, 3, 1, 0), (3, 0, 2, 1))
SLOTS = (("ab", "baseline"), ("ab", "candidate"), ("aa", "baseline"), ("aa", "candidate"))


def williams_rounds(rounds, rng):
    """Per-round 4-run orders: random slot labels, rows shuffled within each block.

    Row order is redrawn until no round starts with the lane that ended the
    previous round, so no lane (and no copy file) ever runs twice in a row.
    """
    labels = rng.sample(SLOTS, 4)
    result, last = [], None
    while len(result) < rounds:
        while True:
            block = rng.sample(WILLIAMS4, 4)
            ends = [last, *(row[-1] for row in block[:-1])]
            if all(row[0] != end for row, end in zip(block, ends)):
                break
        last = block[-1][-1]
        result.extend(tuple(labels[treatment] for treatment in row) for row in block)
    return result[:rounds]


def session(case_ids, measure, seed=0, warmups=1, minimum=5, maximum=30, half_width=.02, roles=None,
            aa_twin=None, copies=None, aa_builds=None, aa_max_full_width_pp=5.0, score_through=False):
    """measure(case, build) returns correctness, ns, shim telemetry, and leftovers.

    With score_through, an incorrect candidate output is recorded and timing
    continues; the result then has correct=False and lists the mismatching cases
    instead of raising IncorrectCandidate on the first mismatch. A mismatching
    unit is timed under the same stopping rule as any other unit. A mismatch on
    any other build still raises.

    With aa_twin, the A/A candidate lane measures that build name: a byte-identical
    copy of the baseline materialized like the candidate, so A/A crosses two files
    exactly as A/B does. Each distinct build then gets the same warm-up count.

    With copies=K (requires aa_twin), every build has K byte-identical copies and
    measure(case, build, copy) is called. Each timed sample draws its copy per
    build from seeded, balanced random blocks of range(K) on a separate random
    stream, so a fixed per-copy (page placement) offset becomes within-session
    noise shared by A/A and A/B instead of a fixed per-lane bias. Orders, the
    stopping rule, the A/A envelope and statistics are unchanged; every copy is
    warmed once per warm-up round.

    With aa_builds=(A1, A2) (requires copies, excludes aa_twin), the four lanes are
    disjoint builds: A/B = baseline vs candidate and A/A = A1 vs A2, each run
    exactly once per round, so every lane and every copy is timed equally and A/A
    shares no lane with A/B. Each round's 4 runs follow a Williams square (slot
    and first-order carry-over balanced per 4-round block). The stopping rule,
    A/A envelope and statistics are unchanged.

    aa_max_full_width_pp is the A/A precision bar (full 95% CI width, in
    percentage points) used by the A/A stopping rule and aa_precision. It
    defaults to 5.0; a task may declare a different bar in tests/judge.json.
    """
    if (type(aa_max_full_width_pp) not in (int, float) or not math.isfinite(aa_max_full_width_pp)
            or not 0 < aa_max_full_width_pp <= 10):
        raise ValueError("A/A precision bar must be finite, positive and at most 10 pp")
    if warmups < 1:
        raise ValueError("At least one warm-up required")
    if aa_twin is not None and (not isinstance(aa_twin, str) or aa_twin in ("baseline", "candidate")):
        raise ValueError("A/A twin must name a build distinct from baseline and candidate")
    if aa_builds is not None and (aa_twin is not None or copies is None or not isinstance(aa_builds, tuple)
                                  or len(aa_builds) != 2 or len(set(aa_builds)) != 2
                                  or not all(isinstance(b, str) for b in aa_builds)
                                  or set(aa_builds) & {"baseline", "candidate"}):
        raise ValueError("Four-lane layout needs two distinct A/A builds, copy rotation and no twin")
    if copies is not None and ((aa_twin is None and aa_builds is None)
                               or type(copies) is not int or not 1 <= copies <= 16):
        raise ValueError("Copy rotation requires an A/A twin or A/A builds and 1 <= copies <= 16")
    roles = roles or {}
    if set(roles) - set(case_ids) or any(role not in ("performance", "correctness") for role in roles.values()):
        raise ValueError("Invalid case role configuration")
    performance = [case for case in case_ids if roles.get(case, "performance") == "performance"]
    if not performance:
        raise ValueError("At least one performance case required for timing")
    rng = random.Random(seed)
    if aa_builds is None:
        schedules = {case: {arm: orders(maximum, rng) for arm in ("ab", "aa")}
                     for case in performance}
    else:
        schedules = {case: williams_rounds(maximum, rng) for case in performance}
    samples, records, summaries = {}, [], {}
    # Copy choice uses its own stream: the order schedule is drawn exactly as without rotation.
    copy_rng = random.Random(f"{seed}:timing-copies")
    copy_blocks = {}

    def draw_copy(case, build):
        block = copy_blocks.setdefault((case, build), [])
        if not block:
            block.extend(copy_rng.sample(range(copies), copies))
        return block.pop()

    incorrect = set()

    def run(case, lane, arm, phase, round_index, copy=None):
        if arm == "ab":
            build = lane
        elif aa_builds is not None:
            build = aa_builds[0] if lane == "baseline" else aa_builds[1]
        else:
            build = (aa_twin or "baseline") if lane == "candidate" else "baseline"
        if copies is None:
            item = measure(case, build)
            extra = {}
        else:
            copy = draw_copy(case, build) if copy is None else copy
            item = measure(case, build, copy)
            extra = {"copy": copy}
        records.append({"case": case, "lane": lane, "build": build, "arm": arm,
                        "phase": phase, "round": round_index, **extra, **item})
        # Nonzero exit, timeout, and streams are compared by collect(), not discarded.
        if not item["correct"]:
            if not score_through or build != "candidate":
                raise IncorrectCandidate(records)
            incorrect.add(case)
        if item["ns"] <= 0:
            raise ValueError("Invalid shim time")
        return item

    # Correctness-only inputs remain mandatory on every session, but consume no
    # timing samples, bootstrap weight or random numbers in the timed schedule.
    for case in case_ids:
        if roles.get(case, "performance") == "correctness":
            for build in ("baseline", "candidate"):
                item = measure(case, build)
                records.append({"case": case, "build": build, "phase": "correctness",
                                "role": "correctness", **item})
                if not item["correct"]:
                    if not score_through or build != "candidate":
                        raise IncorrectCandidate(records)
                    incorrect.add(case)
    for case in performance:
        for _ in range(warmups):
            if aa_twin is None and aa_builds is None:
                for arm in ("ab", "aa"):
                    for lane in rng.sample(["baseline", "candidate"], 2):
                        run(case, lane, arm, "warmup", -1)
            elif copies is None:
                # One warm-up per distinct file: baseline, candidate and A/A twin.
                for arm, lane in rng.sample([("ab", "baseline"), ("ab", "candidate"),
                                             ("aa", "candidate")], 3):
                    run(case, lane, arm, "warmup", -1)
            else:
                # One warm-up per distinct file: every copy of every build.
                lanes = SLOTS if aa_builds is not None else (("ab", "baseline"), ("ab", "candidate"),
                                                              ("aa", "candidate"))
                files = [(arm, lane, copy) for copy in range(copies) for arm, lane in lanes]
                for arm, lane, copy in rng.sample(files, len(files)):
                    run(case, lane, arm, "warmup", -1, copy)
        samples[case] = {"ab": [], "aa": []}
        cpu_samples = {"ab": [], "aa": []}
        for index in range(maximum):
            if aa_builds is None:
                arms = ["ab", "aa"]
                rng.shuffle(arms)
                plan = [(arm, lane) for arm in arms for lane in schedules[case][arm][index]]
            else:
                plan = list(schedules[case][index])
            runs = {"ab": {}, "aa": {}}
            for arm, lane in plan:
                runs[arm][lane] = run(case, lane, arm, "timed", index)
            for arm in dict.fromkeys(arm for arm, _ in plan):
                measured = runs[arm]
                times = {lane: value["ns"] for lane, value in measured.items()}
                samples[case][arm].append((times["baseline"], times["candidate"]))
                cpu = {lane: value.get("resources", {}).get("cpu_time_usec")
                       for lane, value in measured.items()}
                if all(type(value) is int and value > 0 for value in cpu.values()):
                    cpu_samples[arm].append((cpu["baseline"], cpu["candidate"]))
            states = {arm: stopping(pairs, minimum, maximum, half_width, seed + index,
                                    full_width_pp=aa_max_full_width_pp if arm == "aa" else None)
                      for arm, pairs in samples[case].items()}
            aa_low, aa_high = states["aa"].get("ci95", (0, 0))
            ab_low, ab_high = states["ab"].get("ci95", (0, 0))
            if "ci95" in states["aa"]:
                # Same seed as the A/A stopping bootstrap; see aa_bias_test.
                states["aa"]["bias_test"] = aa_bias_test(states["aa"], len(performance), seed + index)
            aa_centered = "bias_test" in states["aa"] and not states["aa"]["bias_test"]["biased"]
            log_floor = max(abs(math.log(aa_low)), abs(math.log(aa_high))) if aa_low > 0 else math.inf
            clears_noise = ab_high < math.exp(-log_floor) or ab_low > math.exp(log_floor)
            resolved = (all(state["status"] == "resolved" for state in states.values())
                        and aa_centered and clears_noise)
            if resolved or index + 1 >= maximum:
                break
        summaries[case] = states
        summaries[case]["session_stop"] = {
            "reason": "precision_and_noise_separation" if resolved else "maximum_pairs",
            "aa_centered": aa_centered, "ab_clears_aa_envelope": clears_noise,
            "pairs": index + 1}
        for arm, pairs in cpu_samples.items():
            if len(pairs) == len(samples[case][arm]):
                summaries[case]["cpu_" + arm] = {
                    **cpu_ratio({case: pairs}, seed),
                    "pairs": [list(pair) for pair in pairs], "sample_count": len(pairs),
                    "total_usec": {"baseline": sum(a for a, _ in pairs),
                                   "candidate": sum(b for _, b in pairs)}}
    aa_passed = all(s["aa"]["status"] == "resolved" and not s["aa"]["bias_test"]["biased"]
                    for s in summaries.values())
    result = {"correct": not incorrect, "cases": summaries, "records": records,
            **({"score_through": True, "incorrect_cases": sorted(incorrect)} if score_through else {}),
            "case_roles": {case: roles.get(case, "performance") for case in case_ids},
            "aa_passed": aa_passed,
            "aa_bias_test": {"method": "bonferroni", "family_alpha": AA_BIAS_FAMILY_ALPHA,
                             "family_size": len(performance),
                             "per_unit_alpha": AA_BIAS_FAMILY_ALPHA / len(performance),
                             "biased_units": sorted(case for case, s in summaries.items()
                                                    if s["aa"]["bias_test"]["biased"])},
            "aa_comparison": (" vs ".join(aa_builds) if aa_builds else
                              "baseline vs " + aa_twin if aa_twin else "baseline vs same baseline"),
            "lane_layout": None if aa_builds is None else {
                "ab": ["baseline", "candidate"], "aa": list(aa_builds),
                "order": "Williams 4x4 per 4-round block; random slot labels per unit"},
            "copy_rotation": None if copies is None else {
                "copies_per_build": copies, "selection": "seeded balanced random blocks per case and build",
                "timed_usage": {f"{build}:{copy}": sum(
                    1 for r in records if r.get("phase") == "timed" and r["build"] == build and r["copy"] == copy)
                    for build in ("baseline", "candidate", *(aa_builds or (aa_twin,)))
                    for copy in range(copies)}},
            "status": "resolved" if aa_passed and all(
                s["session_stop"]["reason"] == "precision_and_noise_separation"
                for s in summaries.values()) else "not resolved",
            "speedup": geometric_mean(s["ab"]["speedup"] for s in summaries.values()),
            "claimable": False}
    result["wall_statistics"] = aggregate_wall(
        {case: state["ab"]["pairs"] for case, state in summaries.items()}, seed)
    result["aa_precision"] = {
        "maximum_full_width_pp": aa_max_full_width_pp,
        "passed": all(state["aa"]["ci95_full_width_pp"] <= aa_max_full_width_pp + 1e-9
                      for state in summaries.values()),
        "units": {case: {"full_width_pp": state["aa"]["ci95_full_width_pp"],
                         "above_bar": state["aa"]["ci95_full_width_pp"] > aa_max_full_width_pp + 1e-9}
                  for case, state in summaries.items()}}
    if all("cpu_ab" in state and "cpu_aa" in state for state in summaries.values()):
        cpu = cpu_ratio({case: state["cpu_ab"]["pairs"] for case, state in summaries.items()}, seed)
        result.update(cpu_ratio=cpu["ratio"], cpu_ratio_ci95=cpu["ci95"],
                      cpu_ratio_direction=cpu["direction"],
                      cpu_classification=("efficiency win" if cpu["ratio"] <= 1 else "parallelism win")
                      if result["speedup"] > 1 else "no wall-time win")
    return result


class IncorrectCandidate(Exception):
    def __init__(self, records):
        self.records = records
        super().__init__("Output contract mismatch")
