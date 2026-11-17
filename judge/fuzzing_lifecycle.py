"""Ownership-aware post-call lifecycle facts; no worker-name exemptions."""
import re
import hashlib


GO_OS_THREAD_ALLOWANCE = 4


def managed_policy(raw):
    policy = raw.get("lifecycle_policy", {"kind": "native-os-threads"})
    if not isinstance(policy, dict):
        raise ValueError("Malformed lifecycle ownership policy")
    return policy


def go_snapshot(snapshot, policy):
    count, maximum = snapshot.get("goroutines"), snapshot.get("gomaxprocs")
    profile = snapshot.get("goroutine_profile_raw", snapshot.get("goroutine_profile"))
    prefixes = policy.get("candidate_module_prefixes")
    if (type(count) is not int or count < 1 or type(maximum) is not int or maximum < 1
            or not isinstance(profile, str) or not profile
            or not isinstance(prefixes, list) or not prefixes
            or not all(isinstance(prefix, str) and prefix for prefix in prefixes)):
        return None
    if (snapshot.get("goroutine_profile_available") is not True
            or snapshot.get("goroutine_profile_sha256") != hashlib.sha256(profile.encode()).hexdigest()):
        return None
    blocks = re.split(r"(?m)(?=^goroutine \d+ \[)", profile)
    blocks = [block for block in blocks if re.match(r"goroutine \d+ \[", block)]
    if len(blocks) != count:
        return None  # A racing/incomplete runtime profile is not invented zero.
    target = [block for block in blocks if any(prefix in block for prefix in prefixes)]
    return {"goroutines": count, "gomaxprocs": maximum, "target_goroutines": len(target)}


def differences(row, policy):
    """Return ownership-unit differences or None when ownership is unmeasured."""
    kind = policy.get("kind")
    if kind == "native-os-threads":
        return []
    if kind != "go-goroutines-v1" or policy.get("native_extensions") is not False:
        return None
    if any(type(row[arm]["before"].get(key)) is not int or row[arm]["before"][key] < 1
           for arm in ("baseline", "candidate") for key in ("goroutines", "gomaxprocs")):
        return None
    left = go_snapshot(row["baseline"]["after"], policy)
    right = go_snapshot(row["candidate"]["after"], policy)
    if left is None or right is None or left["target_goroutines"]:
        return None
    result = []
    if right["gomaxprocs"] != left["gomaxprocs"]:
        result.append("gomaxprocs_changed")
    if right["goroutines"] > left["goroutines"] or right["target_goroutines"]:
        result.append("live_go_goroutines")
    # Idle Go Ms are runtime-owned, not joinable user threads. A new excess
    # above the fixed allowance still blocks; metadata cannot enlarge it.
    limit = right["gomaxprocs"] + GO_OS_THREAD_ALLOWANCE
    if (row["candidate"]["after"]["threads"] > limit
            and row["candidate"]["after"]["threads"] > row["baseline"]["after"]["threads"]):
        identities = row["candidate"]["after"].get("thread_identities")
        if not identities or any(type(value.get("flags")) is not int for value in identities):
            return None  # Cannot blame runtime growth when kernel-exit state is unknown.
        result.append("go_os_thread_allowance_exceeded")
    return result
