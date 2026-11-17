import errno
import subprocess
import sys
import unittest

from tools.fuzz_api_faults.os_errors import parse_os_error


def traceback_for(path, frame="/usr/local/lib/python3.14/tempfile.py",
                  function="_mkstemp_inner", number=30, exception="OSError"):
    return (
        'Traceback (most recent call last):\n'
        '  File "/work/caller.py", line 3, in <module>\n'
        '    NamedTemporaryFile(dir="/work/output")\n'
        f'  File "{frame}", line 257, in {function}\n'
        '    fd = _os.open(file, flags, 0o600)\n'
        f"{exception}: [Errno {number}] Read-only file system: {path!r}\n"
    )


class OSErrorFactsTests(unittest.TestCase):
    def test_two_random_temp_names_are_preserved_not_equated(self):
        rows = []
        for path in ("/work/output/tmp2jg_f30a", "/work/output/tmp8ag_5zz4"):
            text = traceback_for(path)
            row = parse_os_error(text)
            self.assertEqual(row["rawmessage"], text)
            self.assertEqual(row["path"], path)
            self.assertEqual(row["errno"], 30)
            self.assertIsNone(row["code"])
            self.assertEqual(row["exception_class"], "OSError")
            self.assertEqual(row["operation_category"], "unreported")
            self.assertTrue(row["internal_temp_frame"])
            rows.append(row)
        self.assertNotEqual(rows[0], rows[1])

    def test_real_python_tempfile_traceback(self):
        # Exercise real stdlib traceback structure, including its generated
        # filename, without relying on root permissions or a read-only mount.
        program = """
import errno
import tempfile
def fail(path, *args, **kwargs):
    raise OSError(errno.EROFS, "Read-only file system", path)
tempfile._os.open = fail
tempfile.NamedTemporaryFile(dir="/work/output")
"""
        paths = []
        for _ in range(2):
            result = subprocess.run([sys.executable, "-c", program],
                                    capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            row = parse_os_error(result.stderr)
            self.assertIsNotNone(row, result.stderr)
            self.assertEqual(row["errno"], errno.EROFS)
            self.assertTrue(row["internal_temp_frame"], result.stderr)
            self.assertTrue(row["path"].startswith("/work/output/tmp"))
            self.assertEqual(row["rawmessage"], result.stderr)
            paths.append(row["path"])
        self.assertNotEqual(*paths)

    def test_errno_and_path_changes_survive_as_raw_facts(self):
        a = parse_os_error(traceback_for("/work/a"))
        b = parse_os_error(traceback_for("/work/b", number=13))
        self.assertNotEqual(a["path"], b["path"])
        self.assertNotEqual(a["errno"], b["errno"])

    def test_unknown_and_user_temp_paths_are_not_internal(self):
        for frame, function in (
            ("/work/tempfile.py", "_mkstemp_inner"),
            ("/work/_mkstemp_inner.py", "_mkstemp_inner"),
            ("/usr/lib/python3.12/tempfile.py", "user_function"),
            ("/usr/lib/python3.12/other.py", "_mkstemp_inner"),
        ):
            row = parse_os_error(traceback_for("/tmp/tmpabcd", frame, function))
            self.assertFalse(row["internal_temp_frame"])
        row = parse_os_error("OSError: [Errno 30] Read-only file system: '/tmp/tmpabcd'\n")
        self.assertFalse(row["internal_temp_frame"])

    def test_no_frame_provenance_without_traceback_header(self):
        text = (
            '  File "/usr/lib/python3.12/tempfile.py", line 10, in _mkstemp_inner\n'
            "OSError: [Errno 30] Read-only file system: '/tmp/x'\n"
        )
        self.assertFalse(parse_os_error(text)["internal_temp_frame"])

    def test_non_os_valueerror_never_classified(self):
        for text in (
            "ValueError: file '/tmp/x' is read-only\n",
            "ValueError: [Errno 30] Read-only file system: '/tmp/x'\n",
            "ValueError: OSError: [Errno 30] Read-only file system: '/tmp/x'\n",
            "Error: file not found\n",
            "OSError: file not found\n",
            "CustomOSError: [Errno 30] Read-only file system: '/tmp/x'\n",
            "Error: EUSER: file '/tmp/x'\n",
        ):
            self.assertIsNone(parse_os_error(text), text)

    def test_chained_or_multiple_errors_remain_unclassified(self):
        text = traceback_for("/work/a")
        self.assertIsNone(parse_os_error(text + "\n" + traceback_for("/work/b")))
        self.assertIsNone(parse_os_error(text + "ValueError: second failure\n"))
        self.assertIsNone(parse_os_error(
            text + "\nError: EACCES: permission denied, open '/work/b'\n"))

    def test_python_escaped_and_rename_paths(self):
        path = "/work/it's\nquoted"
        row = parse_os_error(traceback_for(path))
        self.assertEqual(row["path"], path)
        text = "PermissionError: [Errno 13] Permission denied: '/work/a' -> '/work/b'\n"
        row = parse_os_error(text)
        self.assertEqual(row["path"], "/work/a")
        self.assertEqual(row["destination_path"], "/work/b")
        self.assertEqual(row["exception_class"], "PermissionError")
        self.assertEqual(row["operation_category"], "unreported")

    def test_no_filename_is_not_invented(self):
        row = parse_os_error("OSError: [Errno 5] Input/output error\n")
        self.assertIsNone(row["path"])
        self.assertIsNone(row["destination_path"])

    def test_node_symbolic_code_and_explicit_operation(self):
        text = (
            "Error: EACCES: permission denied, open '/work/output.json'\n"
            "    at Object.openSync (node:fs:100:3)\n"
        )
        row = parse_os_error(text)
        self.assertEqual(row["code"], "EACCES")
        self.assertIsNone(row["errno"])
        self.assertEqual(row["path"], "/work/output.json")
        self.assertEqual(row["operation_category"], "open")
        self.assertFalse(row["internal_temp_frame"])
        self.assertEqual(row["rawmessage"], text)

    def test_node_unknown_operation_not_guessed(self):
        text = "Error: EROFS: read-only file system\n"
        row = parse_os_error(text)
        self.assertEqual(row["operation_category"], "unreported")
        self.assertIsNone(row["path"])
        self.assertEqual(row["code"], "EROFS")

    def test_node_rename_both_paths_retained(self):
        row = parse_os_error(
            "Error: EACCES: permission denied, rename '/work/a' -> '/work/b'\n")
        self.assertEqual(row["path"], "/work/a")
        self.assertEqual(row["destination_path"], "/work/b")
        self.assertEqual(row["operation_category"], "rename")

    def test_node_second_exception_not_ignored(self):
        self.assertIsNone(parse_os_error(
            "Error: EACCES: permission denied, open '/work/a'\n"
            "ValueError: unrelated failure\n"))

    def test_node_object_metadata_not_silently_ignored(self):
        self.assertIsNone(parse_os_error(
            "Error: EACCES: permission denied, open '/work/a'\n"
            "  code: 'EPERM'\n"))

    def test_text_input_required(self):
        with self.assertRaises(TypeError):
            parse_os_error(b"OSError: [Errno 30] readonly")


if __name__ == "__main__":
    unittest.main()
