"""Normal synchronous multiprocessing shutdown for verifier-owned Python probes."""
from pathlib import Path
import re
import runpy
import sys


def finish_multiprocessing():
    # Progress locks can start CPython's resource tracker even for serial APIs.
    # Its normal EOF exit races PID 1's immediate leftover observation. Run the
    # same finalizers Python runs at exit, then close and join the owned tracker.
    # Never whitelist, sleep for, or kill leftovers. These hooks are tied to the
    # pinned CPython implementation; unmanaged children still reach the shim.
    from multiprocessing import resource_tracker, util
    util._exit_function()
    resource_tracker._resource_tracker._stop()


def script_command(command, driver):
    """Keep the configured interpreter; wrap verifier Python script commands."""
    command = list(command)
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(command[0]).name):
        if len(command) < 2 or not command[1].endswith(".py"):
            raise ValueError("Python probe command must name its verifier script directly")
        return [command[0], driver, *command[1:]]
    return command


def main():
    script = sys.argv[1]
    sys.argv = sys.argv[1:]
    sys.path.insert(0, str(Path(script).parent))
    try:
        runpy.run_path(script, run_name="__main__")
    finally:
        finish_multiprocessing()


if __name__ == "__main__":
    main()
