# Differential harness to implement

Status: not implemented. No coverage or successful differential run is claimed.

Python syntax/diagnostic options, fixes and notebook edge cases; independent lint sessions.

Use the template in `judge/differential_template.py`; configure budgets/seeds in
`tests/judge.json`, keep all harness code verifier-only, and validate at least
three seeds against an unchanged baseline before enabling the harness.
