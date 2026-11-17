"""Host/class identity, SMT sibling checks, and noise counters."""
import hashlib
import json
import os
from pathlib import Path
import platform
import time


def text(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def cpu_list(value):
    result = set()
    for part in value.split(","):
        ends = [int(n) for n in part.split("-")]
        result.update(range(ends[0], ends[-1] + 1))
    return sorted(result)


def pinned(value):
    cores = cpu_list(value)
    if len(cores) != 8 or not set(cores) <= os.sched_getaffinity(0):
        raise ValueError("Exactly eight available pinned logical CPUs required")
    return cores


def host_identity():
    """Comparable across containers on the same kernel, without exposing boot IDs."""
    boot = text("/proc/sys/kernel/random/boot_id")
    return hashlib.sha256(boot.encode()).hexdigest() if boot else None


def fingerprint(cores, cpu_class):
    if not cpu_class.strip():
        raise ValueError("CPU class label required")
    cpu = text("/proc/cpuinfo") or ""
    fields = dict(line.split(":", 1) for line in cpu.splitlines() if ":" in line)
    model = next((v.strip() for k, v in fields.items() if k.strip() == "model name"), None)
    microcode = next((v.strip() for k, v in fields.items() if k.strip() == "microcode"), None)
    value = {"cpu_class": cpu_class, "model": model, "microcode": microcode,
             "kernel": platform.release(), "architecture": platform.machine(),
             "boot_id_sha256": host_identity(), "online_cpu_count": os.cpu_count(),
             "available_cpu_count": len(cores),
             "host_boot_sha256": hashlib.sha256(
                 ((text("/etc/machine-id") or "") + ":" +
                  (text("/proc/sys/kernel/random/boot_id") or "")).encode()).hexdigest(),
             "virtualization": {"hypervisor_flag": "hypervisor" in cpu,
                                "product_name": text("/sys/class/dmi/id/product_name")},
             "cores": cores}
    value["sha256"] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return value


def counters(cores):
    rows = {}
    for line in (text("/proc/stat") or "").splitlines():
        fields = line.split()
        if fields and fields[0].startswith("cpu") and fields[0][3:].isdigit():
            rows[int(fields[0][3:])] = [int(n) for n in fields[1:]]
    frequencies = {str(c): text(f"/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_cur_freq")
                   for c in cores}
    stat = {}
    for line in (text("/sys/fs/cgroup/cpu.stat") or "").splitlines():
        name, value = line.split()
        stat[name] = int(value)
    siblings = {}
    for core in cores:
        value = text(f"/sys/devices/system/cpu/cpu{core}/topology/thread_siblings_list")
        siblings[str(core)] = cpu_list(value) if value else []
    return {"epoch": time.time(), "monotonic": time.monotonic(), "cpu": rows,
            "thread_siblings": siblings,
            "cpu_stat": stat, "frequency_khz": frequencies}


def quality(before, after, cores, max_steal=.01, max_sibling_busy=.10, max_throttled=.01,
            max_frequency_drop=.20):
    siblings = set()
    for core in cores:
        recorded = before.get("thread_siblings", {}).get(str(core))
        if recorded is not None:
            siblings.update(recorded)
        else:
            value = text(f"/sys/devices/system/cpu/cpu{core}/topology/thread_siblings_list")
            if value:
                siblings.update(cpu_list(value))
    siblings -= set(cores)
    busy, steals = [], []
    for core in set(cores) | siblings:
        a, b = before["cpu"].get(core), after["cpu"].get(core)
        if a is None or b is None:
            continue
        delta = [y - x for x, y in zip(a, b)]
        total = sum(delta[:8])
        if total > 0:
            if core in cores:
                steals.append(delta[7] / total if len(delta) > 7 else 0)
            else:
                busy.append(1 - (delta[3] + delta[4]) / total)
    elapsed = after["monotonic"] - before["monotonic"]
    throttled = (after["cpu_stat"].get("throttled_usec", 0) -
                 before["cpu_stat"].get("throttled_usec", 0)) / max(elapsed * 1e6, 1)
    result = {"steal_fraction": max(steals, default=0),
              "sibling_busy_fraction": max(busy, default=0), "throttled_fraction": throttled,
              "missing_cpu_counters": not bool(steals),
              "frequency_before": before["frequency_khz"], "frequency_after": after["frequency_khz"]}
    drops = []
    for core, start in before["frequency_khz"].items():
        end = after["frequency_khz"].get(core)
        if start and end and int(start) > 0:
            drops.append(max(0, 1 - int(end) / int(start)))
    result["frequency_drop_fraction"] = max(drops) if drops else None
    result["noisy"] = (result["missing_cpu_counters"] or result["steal_fraction"] > max_steal or
                       result["sibling_busy_fraction"] > max_sibling_busy or throttled > max_throttled or
                       max(drops, default=0) > max_frequency_drop)
    return result
