"""Shared headline/class contract used by CLI and generic fault oracles."""
from tools.runner.runner import normalize_cli_observation, normalize_stderr, stderr_contract

__all__ = ["normalize_stderr", "stderr_contract", "normalize_cli_observation"]
