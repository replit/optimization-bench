import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from judge.fuzzing_generic import _thread_snapshot

ROOT = Path(__file__).resolve().parents[2]


class NativeLifecycleTests(unittest.TestCase):
    def test_real_per_call_fd_and_thread_growth_are_observed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "test.cpp"
            source.write_text(
                '#include "native_lifecycle.hpp"\n#include <iostream>\n#include <fcntl.h>\n'
                '#include <thread>\n#include <atomic>\n'
                'int main() { std::cout << optimbench_lifecycle::snapshot() << "\\n";'
                'int fd = open("/dev/null", O_RDONLY); std::atomic<bool> ready{false}, stop{false};'
                'std::thread t([&] { ready=true; while(!stop) usleep(1000); });'
                'while(!ready) usleep(1000);'
                'std::cout << optimbench_lifecycle::snapshot() << "\\n";'
                'stop=true; t.join(); close(fd);'
                'std::cout << optimbench_lifecycle::snapshot() << "\\n"; }\n')
            executable = root / "probe"
            subprocess.run(["c++", "-std=c++17", "-pthread",
                            "-I" + str(ROOT / "tools/fuzz_api_faults"), str(source), "-o", str(executable)],
                           check=True, capture_output=True)
            before, leaked, restored = [json.loads(line) for line in subprocess.check_output(
                [str(executable)], text=True).splitlines()]
            for row in (before, leaked, restored):
                self.assertTrue(row["available"], row)
                self.assertIsNotNone(_thread_snapshot(row))
            self.assertEqual(leaked["fds"], before["fds"] + 1)
            self.assertEqual(leaked["threads"], before["threads"] + 1)
            self.assertEqual(restored["fds"], before["fds"])
            self.assertEqual(restored["threads"], before["threads"])

if __name__ == "__main__":
    unittest.main()
