"""Real native exit snapshots: joined workers vs concrete surviving identities."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from judge import process_state


@unittest.skipUnless(shutil.which("cc") and Path("/proc/self/task").is_dir(), "Linux C compiler required")
class NativeIdentityTests(unittest.TestCase):
    def test_child_counter_sees_live_child_then_zero_after_explicit_reap(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            observer = Path(process_state.__file__).with_name("process_probe.c")
            source = root / "child.c"
            # Call the observer once explicitly as a counter unit test, then
            # exercise its real destructor after killing/reaping the control.
            source.write_text('#include "' + str(observer) + '"\n' + r'''
#include <signal.h>
#include <sys/wait.h>
int main(void) {
    pid_t child = fork();
    if (child < 0) return 2;
    if (!child) { for (;;) pause(); }
    final_state();
    if (kill(child, SIGKILL) || waitpid(child, 0, 0) != child) return 3;
    return 0;
}
''')
            binary, output = root / "child", root / "child.jsonl"
            subprocess.run(["cc", str(source), "-o", str(binary), "-lm"], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, timeout=5, capture_output=True,
                           env={"PATH": os.defpath, "LC_ALL": "C", "HOME": str(root),
                                "OPTIMBENCH_STATE_FILE": str(output), "OPTIMBENCH_STATE_STEP": "0"})
            rows = [json.loads(row) for row in output.read_text().splitlines()]
            self.assertEqual([row["children"] for row in rows], [1, 0])

    def test_joined_positive_detached_negative_and_identity_count_consistency(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "control.c"
            source.write_text(r'''
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <string.h>
#include <unistd.h>
static atomic_int ready;
static int leak;
static void *worker(void *unused) {
    (void)unused;
    atomic_store(&ready, 1);
    if (leak) sleep(2);
    return 0;
}
int main(int argc, char **argv) {
    pthread_t t;
    leak = argc > 1 && strcmp(argv[1], "leak") == 0;
    if (pthread_create(&t, 0, worker, 0)) return 2;
    while (!atomic_load(&ready)) sched_yield();
    if (leak) return pthread_detach(t);
    return pthread_join(t, 0);
}
''')
            library, binary = root / "observer.so", root / "control"
            subprocess.run(["cc", "-shared", "-fPIC", str(Path(process_state.__file__).with_name("process_probe.c")),
                            "-o", str(library), "-lm"], check=True, capture_output=True)
            subprocess.run(["cc", "-std=c11", "-pthread", str(source), "-o", str(binary)],
                           check=True, capture_output=True)
            for mode, repetitions in (("joined", 16), ("leak", 2)):
                for index in range(repetitions):
                    output = root / f"{mode}-{index}.jsonl"
                    env = {"PATH": os.defpath, "LC_ALL": "C", "HOME": str(root),
                           "LD_PRELOAD": str(library), "OPTIMBENCH_STATE_FILE": str(output),
                           "OPTIMBENCH_STATE_STEP": "7"}
                    subprocess.run([str(binary), mode], env=env, check=True, capture_output=True, timeout=5)
                    rows = [json.loads(row) for row in output.read_text().splitlines()]
                    self.assertEqual(len(rows), 1)
                    value = rows[0]
                    identity = value["thread_identity"]
                    self.assertEqual(identity["step"], "7")
                    self.assertEqual(identity["boundary"], "native-destructor")
                    self.assertGreater(identity["sampled_monotonic_ns"], 0)
                    self.assertEqual(value["threads"], len(identity["threads"]))
                    self.assertTrue(all(row["start_ticks"] > 0 and row["tgid"] == identity["pid"]
                                        and row["state"] not in ("Z", "X") and not row["flags"] & 4
                                        for row in identity["threads"]))
                    self.assertEqual(value["threads"], 1 if mode == "joined" else 2)
                    self.assertEqual(value["children"], 0)
                    self.assertEqual(value["children_method"], "private-proc-ppid-live-scan")


if __name__ == "__main__":
    unittest.main()
