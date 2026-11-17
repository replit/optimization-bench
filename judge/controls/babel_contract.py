"""Expected exit/status contracts for the two frozen archived Babel arms."""


def archived_passed(mode, code, result):
    if mode not in ("historical120", "target117"):
        raise ValueError("Unknown archived Babel arm")
    expected_code = 0 if mode == "historical120" else 1
    expected_counts = (120, 0, 120) if mode == "historical120" else (109, 8, 117)
    return (code == expected_code and result["exit_code"] == expected_code and
            (result["passed"], result["failed"], result["total"]) == expected_counts and
            result["identities_match"] and result["fixtures_unchanged"])
