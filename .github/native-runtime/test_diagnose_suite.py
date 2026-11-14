"""Headless supervisor tests using temporary fixtures and synthetic processes."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

SCRIPT = Path(__file__).with_name("diagnose-suite.py")
SPEC = importlib.util.spec_from_file_location("diagnose_suite", SCRIPT)
diag = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diag)

STUBBORN_GROUP = r"""
import os, signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
pid = os.fork()
os.write(1, f"{'root' if pid else 'descendant'} {os.getpid()}\n".encode())
time.sleep(30)
"""


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.cwd = Path(scratch.name)
        self.output = self.cwd / "diagnostic"
        self.log = self.output / "product-tests.log"

    def command(self, body, timeout=8):
        # The timeout check runs first. Integrations cannot invoke native helpers.
        return [
            sys.executable, "-I", str(SCRIPT.resolve()),
            "--output", str(self.output), "--timeout", str(timeout), "--sample-after", str(timeout),
            "--", sys.executable, "-E", "-s", "-u", "-c", body,
        ]

    def run_child(self, body):
        return subprocess.run(self.command(body), cwd=self.cwd, stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=12)

    def receipt(self):
        return json.loads((self.output / "receipt.json").read_text())

    def group_ids(self):
        if not self.log.exists():
            return {}
        complete = self.log.read_text().split("\n")[:-1]
        return {key: int(value) for key, value in (line.split() for line in complete)}

    def verify_group_cleanup(self, proc, expected, sig=None):
        root = None
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                ids = self.group_ids()
                root = ids.get("root", root)
                if len(ids) == 2:
                    break
                time.sleep(0.02)
            self.assertEqual(set(ids), {"root", "descendant"})
            self.assertEqual(os.getpgid(ids["descendant"]), root)
            if sig:
                proc.send_signal(sig)
            proc.communicate(timeout=9)
            self.assertEqual(proc.returncode, expected, self.receipt())
            self.assertEqual(self.receipt()["child_exit"], -signal.SIGKILL)
            self.assertTrue(self.receipt()["group_cleanup"])
            with self.assertRaises(ProcessLookupError):
                os.killpg(root, 0)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.communicate(timeout=6)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate(timeout=3)
            if root is not None:
                try:
                    os.killpg(root, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def start_stubborn_group(self, timeout):
        return subprocess.Popen(self.command(STUBBORN_GROUP, timeout), cwd=self.cwd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_zero_streams_both_channels_and_preserves_cwd(self):
        (self.cwd / "synthetic_module.py").write_text("VALUE = 'synthetic stdout'\n")
        result = self.run_child(
            "import sys, synthetic_module; assert sys.stdin.read() == ''; "
            "print(synthetic_module.VALUE); print('synthetic stderr', file=sys.stderr)"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"synthetic stdout\nsynthetic stderr\n")
        self.assertEqual(self.log.read_bytes(), result.stdout)
        self.assertEqual(self.receipt()["child_exit"], 0)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.log.stat().st_mode), 0o600)

    def test_child_nonzero_is_preserved(self):
        result = self.run_child("print('synthetic failure'); raise SystemExit(37)")
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertEqual(self.log.read_text(), "synthetic failure\n")
        self.assertEqual(self.receipt()["child_exit"], 37)

    def test_existing_directory_and_symlink_are_rejected(self):
        self.output.mkdir()
        self.assertNotEqual(self.run_child("raise SystemExit(0)").returncode, 0)
        self.assertEqual(list(self.output.iterdir()), [])
        self.output.rmdir()
        target = self.cwd / "missing"
        self.output.symlink_to(target, target_is_directory=True)
        self.assertNotEqual(self.run_child("raise SystemExit(0)").returncode, 0)
        self.assertTrue(self.output.is_symlink())
        self.assertFalse(target.exists())

    def test_timeout_kills_stubborn_group(self):
        self.verify_group_cleanup(self.start_stubborn_group(2), 124)
        self.assertEqual(self.receipt()["reason"], "timeout")

    def test_sigterm_kills_stubborn_group(self):
        self.verify_group_cleanup(self.start_stubborn_group(8), 143, signal.SIGTERM)
        self.assertEqual(self.receipt()["signal"], signal.SIGTERM)

    def test_sigint_kills_stubborn_group(self):
        self.verify_group_cleanup(self.start_stubborn_group(8), 130, signal.SIGINT)
        self.assertEqual(self.receipt()["signal"], signal.SIGINT)

    def test_exited_root_with_inherited_output_descriptor_is_failure(self):
        result = self.run_child(
            "import os, time; pid = os.fork(); "
            "time.sleep(30) if pid == 0 else print('root finished')"
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text(), "root finished\n")
        self.assertEqual(self.receipt()["child_exit"], 0)
        self.assertTrue(self.receipt()["group_cleanup"])

    def test_output_is_hard_bounded_even_with_fast_writer(self):
        # Discard mirrored console bytes to keep the test process memory bounded.
        argv = self.command("import os; data = b'x' * 65536;\nwhile True: os.write(1, data)")
        result = subprocess.run(argv, cwd=self.cwd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=12)
        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertEqual(self.log.stat().st_size, diag.MAX_OUTPUT)
        self.assertEqual(self.receipt()["reason"], "output limit reached")
        self.assertTrue(self.receipt()["group_cleanup"])


class SamplingTests(unittest.TestCase):
    def test_only_first_six_owned_pids_rechecked_and_errors_recorded(self):
        inventory = (
            "501 1 500 /synthetic/descendant\n"
            "99 1 99 /synthetic/unrelated\n"
            "500 1 500 /synthetic/leader\n"
            + "".join(f"{pid} 500 500 /synthetic/worker\n" for pid in range(502, 508))
        )

        proc = mock.Mock()
        proc.poll.return_value = 1
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(diag, "ps", return_value=subprocess.CompletedProcess([], 0, inventory)), \
                mock.patch.object(diag.os, "getpgid", side_effect=lambda pid: 999 if pid == 501 else 500) as pgid, \
                mock.patch.object(diag, "spawn_limited",
                                  side_effect=[FileNotFoundError(2, "unused"), proc, proc, proc, proc]) as launch:
            output = Path(directory)
            round_ = diag.SampleRound(output, 500)
            round_.start()
            for _ in range(14):
                round_.tick()
            self.assertEqual(round_.receipt["matched"], 8)
            self.assertEqual(round_.receipt["status"], "finished")
            self.assertEqual([call.args[0] for call in pgid.call_args_list], list(range(500, 506)))
            self.assertEqual([int(call.args[0][1]) for call in launch.call_args_list],
                             [500, 502, 503, 504, 505])
            self.assertEqual([row["status"] for row in round_.receipt["samples"]], [
                "FileNotFoundError (errno 2)", "no longer in group",
                *(["sample exit 1 (tool or permission error)"] * 4),
            ])
            rows = (output / "process-group.txt").read_text().splitlines()
            self.assertEqual(len(rows), 7)
            self.assertIn("500 1 500 leader", rows)
            self.assertNotIn("99 1 99 unrelated", rows)
            self.assertFalse(any("/synthetic" in row for row in rows))

    def test_sample_artifact_contains_only_call_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "sample.raw"
            stack = Path(directory) / "stack.txt"
            raw.write_bytes(
                b"Path: /synthetic/private/executable\nCall graph:\n"
                b"    +  1 synthetic_function (/synthetic/private/source.c:42)\n\n"
                b"Total number in stack (recursive counted multiple, when >=5):\n"
                b"Binary Images:\n  /synthetic/private/module"
            )
            diag.stack_only(raw, stack)
            self.assertEqual(stack.read_bytes(),
                             b"Call graph:\n    +  1 synthetic_function (source.c:42)\n")

    def test_sample_deadline_kills_only_the_helper(self):
        proc = mock.Mock()
        proc.poll.side_effect = [None, None, -signal.SIGKILL]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            raw = output / ".sample-500.raw"
            raw.write_text("partial synthetic data")
            round_ = diag.SampleRound(output, 500)
            round_.active = (500, proc, raw, time.monotonic() - 1)
            round_.tick()
            proc.kill.assert_called_once_with()
            proc.wait.assert_called_once_with(timeout=2)
            self.assertIsNone(round_.active)
            self.assertFalse(raw.exists())
            self.assertEqual(round_.receipt["samples"], [{"pid": 500, "status": "sample timed out"}])


if __name__ == "__main__":
    unittest.main()
