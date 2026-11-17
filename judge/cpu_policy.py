"""RC CPU contract: correctness across subsets, official timing at eight CPUs."""
CORRECTNESS_CPU_COUNTS = (1, 2, 4, 8)
OFFICIAL_TIMING_CPU_COUNT = 8
LOW_CORE_TIMING_DEFAULT = False
VERSION = "rc-cpu-policy-v1"


def release_policy():
    return {"version": VERSION,
            "correctness": {"mode": "gated", "cpu_counts": list(CORRECTNESS_CPU_COUNTS)},
            "official_timing": {"cpu_count": OFFICIAL_TIMING_CPU_COUNT},
            "low_core_timing": {"mode": "report-only", "cpu_counts": [1, 2, 4],
                                "enabled_by_default": LOW_CORE_TIMING_DEFAULT,
                                "executed_by_official_judge": False}}


def correctness_receipt(receipt):
    """Fail closed on missing/unverified subset execution; never infer from flags."""
    if not isinstance(receipt, dict) or receipt.get("timing_performed") is not False:
        return {"passed": False, "reason": "missing_untimed_cpu_subset_receipt"}
    records = receipt.get("records", [])
    if not isinstance(records, list):
        return {"passed": False, "reason": "malformed_cpu_subset_records"}
    by_count = {row.get("cpu_count"): row for row in records
                if isinstance(row, dict) and type(row.get("cpu_count")) is int}
    missing, failed = [], []
    for count in CORRECTNESS_CPU_COUNTS:
        row = by_count.get(count)
        if row is None:
            missing.append(count)
            continue
        cpus = row.get("actual_cpus")
        if (row.get("passed") is not True or row.get("affinity_verified") is not True or
                not isinstance(cpus, list) or
                not all(type(cpu) is int and cpu >= 0 for cpu in cpus) or len(set(cpus)) != count or
                type(row.get("baseline_cases")) is not int or row["baseline_cases"] < 1 or
                type(row.get("candidate_cases")) is not int or row["candidate_cases"] < 1):
            failed.append(count)
    return {"passed": not missing and not failed, "version": VERSION,
            "required_cpu_counts": list(CORRECTNESS_CPU_COUNTS),
            "missing_cpu_counts": missing, "failed_cpu_counts": failed}
