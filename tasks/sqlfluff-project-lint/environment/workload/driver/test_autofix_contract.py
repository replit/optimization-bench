"""Protected additions to the unchanged upstream SQLFluff test suite."""
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from unittest import mock

import pytest
from sqlfluff.core.linter import LintedFile
import sqlfluff.core.linter.linted_file as implementation


def test_partial_write_failure_preserves_original_and_removes_temporary(tmp_path):
    """Use the existing upstream helper entrypoint, but a kernel-level failure."""
    path = tmp_path / "original.sql"
    path.write_text("original")
    child = """
import errno, resource, signal, sys
from sqlfluff.core.linter import LintedFile
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
resource.setrlimit(resource.RLIMIT_FSIZE, (4096, 4096))
try:
    LintedFile._safe_create_replace_file(sys.argv[1], sys.argv[1], "x" * 8192, "utf-8")
except OSError as error:
    assert error.errno == errno.EFBIG
    print("expected_write_failure")
else:
    raise AssertionError("The file-size limit did not reject the write")
"""
    result = subprocess.run([sys.executable, "-c", child, str(path)],
                            capture_output=True, timeout=30,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.returncode == 0, result.stderr
    assert result.stdout == b"expected_write_failure\n"
    assert path.read_text() == "original"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["original.sql"]


@pytest.mark.parametrize("mode", [0o600, 0o640, 0o444])
def test_file_permissions_survive_replacement(mode, tmp_path):
    path = tmp_path / "original.sql"
    path.write_text("original")
    path.chmod(mode)
    LintedFile._safe_create_replace_file(str(path), str(path), "updated", "utf-8")
    assert path.read_text() == "updated"
    assert stat.S_IMODE(path.stat().st_mode) == mode
    assert sorted(p.name for p in tmp_path.iterdir()) == ["original.sql"]


def assert_sync_before_replacement(path, operation, alternate_replacement=False):
    """Accept either Python data-sync API and equivalent replacement paths.

    The final data inode must have finished syncing while the original
    destination still existed. Additional sync calls and replacement APIs do
    not affect the guarantee. Original upstream tests stay intact.
    """
    path.write_text("original")
    original_identity = (path.stat().st_dev, path.stat().st_ino)
    synced = set()
    real_fsync, real_fdatasync = os.fsync, os.fdatasync

    def observe(fd, sync):
        info = os.fstat(fd)
        complete = False
        if stat.S_ISREG(info.st_mode):
            with open(f"/proc/self/fd/{fd}", "rb") as stream:
                complete = stream.read() == b"updated"
        sync(fd)
        original_intact = (
            path.exists()
            and (path.stat().st_dev, path.stat().st_ino) == original_identity
            and path.read_bytes() == b"original"
        )
        if complete and original_intact:
            synced.add((info.st_dev, info.st_ino))

    def alternate_sync(fd):
        real_fdatasync(fd)
        real_fdatasync(fd)

    replacement = os.replace if alternate_replacement else implementation.shutil.move
    sync = alternate_sync if alternate_replacement else real_fsync
    with mock.patch.object(os, "fsync", side_effect=lambda fd: observe(fd, sync)), \
            mock.patch.object(os, "fdatasync", side_effect=lambda fd: observe(fd, real_fdatasync)), \
            mock.patch.object(implementation.shutil, "move", side_effect=replacement):
        operation(path)
    assert path.read_text() == "updated"
    assert (path.stat().st_dev, path.stat().st_ino) in synced, "data sync must finish before replacement"


@pytest.mark.parametrize("alternate_replacement", [False, True])
def test_complete_result_has_finished_data_sync(tmp_path, alternate_replacement):
    assert_sync_before_replacement(
        tmp_path / "original.sql",
        lambda path: LintedFile._safe_create_replace_file(str(path), str(path), "updated", "utf-8"),
        alternate_replacement,
    )


def test_replace_then_sync_is_rejected(tmp_path):
    def unsafe_replacement(path):
        temporary = path.with_suffix(".temporary")
        with temporary.open("w") as stream:
            stream.write("updated")
            stream.flush()
            os.replace(temporary, path)
            os.fsync(stream.fileno())

    with pytest.raises(AssertionError, match="data sync must finish before replacement"):
        assert_sync_before_replacement(tmp_path / "original.sql", unsafe_replacement)


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_cli_replaces_link_entry_without_changing_its_target(link_kind, tmp_path):
    target, entry = tmp_path / "target.sql", tmp_path / "entry.sql"
    original = b"select  1\n"
    target.write_bytes(original)
    if link_kind == "symlink":
        entry.symlink_to("target.sql")
    else:
        os.link(target, entry)
    neighbor = tmp_path / "README.txt"
    neighbor.write_bytes(b"do not change\n")
    target_before, neighbor_before = target.stat(), neighbor.stat()
    result = subprocess.run(
        [sys.executable, "-m", "sqlfluff", "fix", "entry.sql",
         "--dialect=ansi", "--rules=LT01,LT12,CP01", "--processes=1",
         "--disable-progress-bar", "--nocolor", "--ignore-local-config"],
        cwd=tmp_path, capture_output=True, timeout=30,
    )
    assert result.returncode == 0
    # The unchanged upstream example plugin emits a startup warning when the
    # full suite installs it. Links must match the ordinary-file CLI contract,
    # including that real warning, rather than require an empty stderr.
    with tempfile.TemporaryDirectory(dir=tmp_path.parent) as control_directory:
        control_root = Path(control_directory)
        (control_root / "entry.sql").write_bytes(original)
        control = subprocess.run(result.args, cwd=control_root,
                                 capture_output=True, timeout=30)
    assert control.returncode == 0
    assert result.stdout == control.stdout
    assert result.stderr == control.stderr
    assert entry.read_bytes() == b"select 1\n"
    assert not entry.is_symlink()
    assert target.read_bytes() == original
    assert entry.stat().st_ino != target.stat().st_ino
    assert entry.stat().st_nlink == target.stat().st_nlink == 1
    for field in ("st_ino", "st_mode", "st_uid", "st_gid", "st_size", "st_mtime_ns"):
        assert getattr(target.stat(), field) == getattr(target_before, field)
    if link_kind == "symlink":
        assert target.stat().st_ctime_ns == target_before.st_ctime_ns
    assert neighbor.read_bytes() == b"do not change\n"
    for field in ("st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
                  "st_size", "st_mtime_ns", "st_ctime_ns"):
        assert getattr(neighbor.stat(), field) == getattr(neighbor_before, field)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["README.txt", "entry.sql", "target.sql"]
