"""Read-only build aliases for untimed probes of relocated build products."""
from pathlib import Path


def mounts(build):
    """Rebind both verifier build-time roots to the build being observed.

    Generated launchers (notably libtool) can retain absolute library paths.
    /app/build is the probe API; /scratch/build is judge.build's output root,
    and /app/baseline-build is the image recipe's pristine output root. Neither
    alias may resolve to another build, including an image's pristine copy.
    These aliases belong only to observation containers, never build containers
    where /scratch/build must remain writable.
    """
    root = Path(build).resolve()
    return [argument for target in ("/scratch/build", "/app/baseline-build")
            for argument in ("-v", f"{root}:{target}:ro")]
