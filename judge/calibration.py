"""Same-host, same-image A/A receipt required within the preceding 24 hours."""
import time


def require_measurement(session):
    if (session.get("status") not in ("resolved", "not resolved")
            or "correct" not in session or "aa_passed" not in session
            or not isinstance(session.get("quality"), dict)
            or "noisy" not in session["quality"]):
        raise ValueError("no calibration performed: gate result " +
                         str(session.get("status", "unknown")))


def receipt(session, host, image, now=None):
    require_measurement(session)
    return {"format": "judge-calibration-v1", "created_epoch": time.time() if now is None else now,
            "host_sha256": host["sha256"], "cpu_class": host["cpu_class"], "image": image,
            "baseline_build_sha256": session.get("baseline_build_sha256"),
            "reference_key": session.get("reference_key"),
            "passed": bool(session["correct"] and session["aa_passed"] and
                           session["status"] == "resolved" and not session["quality"]["noisy"]),
            "claimable": False}


def from_session(session, now=None):
    # Early gate results intentionally omit measurement host/quality fields.
    # Reject those before looking up host or attempting to write a receipt.
    require_measurement(session)
    if (not session.get("baseline_build_sha256")
            or session["baseline_build_sha256"] != session.get("candidate_build_sha256")):
        raise ValueError("A/A calibration must compare identical baseline builds")
    host = session.get("host")
    if (not isinstance(host, dict) or not host.get("sha256")
            or not host.get("cpu_class") or not session.get("image")):
        raise ValueError("no calibration performed: incomplete measurement host/image")
    return receipt(session, host, session["image"], now)


def require(calibration, host, image, now=None, baseline_build_sha256=None, reference_key=None):
    age = (time.time() if now is None else now) - calibration["created_epoch"]
    if (calibration.get("format") != "judge-calibration-v1" or not calibration["passed"]
            or not 0 <= age <= 86400 or calibration["host_sha256"] != host["sha256"]
            or calibration["cpu_class"] != host["cpu_class"] or calibration["image"] != image
            or (baseline_build_sha256 is not None and
                calibration.get("baseline_build_sha256") != baseline_build_sha256)
            or (reference_key is not None and calibration.get("reference_key") != reference_key)):
        raise ValueError("Passing A/A calibration on this CPU/image within 24h required")


if __name__ == "__main__":
    import argparse
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path, help="Verifier result from baseline vs same baseline")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = json.loads(args.session.read_text())
    try:
        result = from_session(value)
    except ValueError as error:
        parser.error(str(error))
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    raise SystemExit(0 if result["passed"] else 1)
