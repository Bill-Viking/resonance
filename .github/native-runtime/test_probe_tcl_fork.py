"""Synthetic tests: no native Tcl, GUI, installed storage, or real sampling."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SCRIPT = Path(__file__).with_name("probe-tcl-fork.py").resolve()
EMITTER = SCRIPT.with_name("emit-evidence.py")
SPEC = importlib.util.spec_from_file_location("probe_tcl_fork", SCRIPT)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
SECRET = "/synthetic/never-mirror"


HARNESS = r"""
import builtins, importlib.util, os, signal, sys
from pathlib import Path
script, fixture, output, case, pid_dir = sys.argv[1:]
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    assert name not in {"tkinter", "_tkinter"}
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
spec = importlib.util.spec_from_file_location("probe", script)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
load_recipe = probe.recipe
if case == "sample-cleanup":
    def load_for_test(name):
        recipe = load_recipe(name)
        if name == "diagnose-suite.py":
            class SyntheticSample(recipe.SampleRound):
                def start(self):
                    self.receipt.update(status="started", matched=1)
                    proc = recipe.spawn_limited(
                        [sys.executable, "-I", "-c", "import time; time.sleep(30)"],
                        recipe.MAX_STACK, stdout=recipe.subprocess.DEVNULL,
                        stderr=recipe.subprocess.DEVNULL,
                    )
                    raw = self.output / ".sample-1.raw"
                    self.active = (1, proc, raw, float("inf"))
                    (Path(pid_dir) / "sample").write_text(str(proc.pid))
            def failed_stop(proc):
                raise recipe.subprocess.TimeoutExpired("synthetic", 2)
            recipe.SampleRound = SyntheticSample
            recipe.stop_sample = failed_stop
        return recipe
    probe.recipe = load_for_test
    probe.SAMPLE_AFTER = 0.1
    probe.ARM_SECONDS = 0.75
else:
    probe.ARM_SECONDS = 0.75
# The global test delivers SIGALRM itself once its worker has started. This
# avoids a timing race with the supervisor's per-arm timeout check.
probe.WORKER_SCRIPT = fixture
old = signal.signal(signal.SIGALRM, signal.SIG_IGN)
signal.setitimer(signal.ITIMER_REAL, 20)
try:
    rc = probe.coordinate(Path(output), sys.executable)
    assert "tkinter" not in sys.modules and "_tkinter" not in sys.modules
    assert signal.getsignal(signal.SIGALRM) == signal.SIG_IGN
    assert 0 < signal.getitimer(signal.ITIMER_REAL)[0] <= 20
finally:
    signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, old)
sys.exit(rc)
"""


class WorkerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def test_same_fixed_commands_and_tcl_lifetimes(self):
        for arm in probe.ARMS:
            with self.subTest(arm=arm):
                cwd = self.root / arm
                cwd.mkdir()
                alive = set()

                class Tcl:
                    def __init__(self):
                        alive.add(id(self))

                    def __del__(self):
                        alive.remove(id(self))

                    def call(self, *args):
                        self_args.assertEqual(args, ("info", "patchlevel"))
                        return "9.0.4"

                self_args = self
                def run(argv, **kwargs):
                    self.assertEqual(alive != set(), arm == "retained-tcl")
                    self.assertEqual(argv, ["/usr/bin/true"])
                    self.assertEqual(kwargs, {
                        "cwd": cwd, "env": {"LC_ALL": "C"}, "stdin": subprocess.DEVNULL,
                        "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT,
                        "close_fds": True, "check": True, "timeout": 2,
                    })

                fake = mock.Mock(side_effect=Tcl)
                with mock.patch.dict(sys.modules, {"tkinter": SimpleNamespace(Tcl=fake)}), \
                        mock.patch.object(probe, "RETAINED_TCL", None), \
                        mock.patch.object(Path, "cwd", return_value=cwd), \
                        mock.patch.object(subprocess, "run", side_effect=run) as calls:
                    self.assertEqual(probe.worker(arm), 0)
                    self.assertEqual(calls.call_count, 1000)
                    self.assertEqual(fake.call_count, int(arm != "no-tcl"))
                    self.assertEqual(bool(alive), arm == "retained-tcl")
                data = probe.observations(cwd, arm, probe.recipe("emit-evidence.py"))
                self.assertEqual(data["count"], 1000)
                self.assertEqual(data["worker_status"], "complete")
                self.assertEqual(data["tcl_patch"], None if arm == "no-tcl" else "9.0.4")

    def test_worker_timeout_reports_only_fixed_label_and_completed_count(self):
        with mock.patch.object(Path, "cwd", return_value=self.root), \
                mock.patch.object(subprocess, "run",
                                  side_effect=[None, subprocess.TimeoutExpired(SECRET, 2)]):
            self.assertEqual(probe.worker("no-tcl"), 1)
        self.assertNotIn(SECRET, (self.root / probe.PROGRESS).read_text())
        data = probe.observations(self.root, "no-tcl", probe.recipe("emit-evidence.py"))
        self.assertEqual((data["count"], data["worker_status"]), (1, "timeout"))

    def test_budget_caps_and_cleanup_reserve(self):
        with mock.patch.object(probe.time, "monotonic", return_value=0):
            budget = probe.Budget()
            self.assertEqual(budget.arm_seconds(), 35)
        with mock.patch.object(probe.time, "monotonic", return_value=108):
            self.assertLessEqual(probe.ALARM_SECONDS, 111)
            self.assertGreater(budget.arm_seconds(), 0)
            self.assertLessEqual(budget.arm_seconds(), 120 - 108 - 9)
        with mock.patch.object(probe.time, "monotonic", return_value=111):
            self.assertEqual(budget.stop_reason(), "global-budget")

    def test_os_timer_interrupts_a_safe_poll(self):
        budget = probe.Budget()
        old = signal.signal(signal.SIGALRM, budget.alarm)
        try:
            signal.setitimer(signal.ITIMER_REAL, 0.02)
            with self.assertRaises(TimeoutError) as caught:
                budget.poll_sleep(0.5)
            self.assertEqual(caught.exception.errno, probe.errno.ETIMEDOUT)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old)


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.addCleanup(self.reap_test_groups)
        self.output = self.root / "probe"
        self.fixture = self.root / "controlled.py"

    def reap_test_groups(self):
        for arm in (*probe.ARMS, "sample"):
            path = self.root / arm
            if path.exists():
                try:
                    os.killpg(int(path.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def run_case(self, case):
        self.fixture.write_text(f"""import json, os, signal, sys, time
from pathlib import Path
arm = sys.argv[-1]
case = {case!r}
assert os.environ.get('SYNTHETIC_SECRET') is None
assert sys.stdin.read() == ""
with open('observations', 'x', buffering=1) as log:
    def record(value):
        print(json.dumps(value), file=log, flush=True)
    record({probe.runtime()!r})
    record({{'tcl_patch': None if arm == 'no-tcl' else '9.0.4'}})
    if arm == 'no-tcl':
        print({SECRET!r}, flush=True)
        print({SECRET!r}, file=sys.stderr, flush=True)
        if case in {{'timeout', 'signal-int', 'signal-term', 'global', 'sample-cleanup'}}:
            record(1)
            Path({str(self.root)!r}, arm).write_text(str(os.getpid()))
            sig = {{'signal-int': signal.SIGINT, 'signal-term': signal.SIGTERM,
                    'global': signal.SIGALRM}}.get(case)
            if sig:
                os.kill(os.getppid(), sig)
            time.sleep(30)
        if case == 'failed':
            record(1)
            record('command-error')
            raise SystemExit(1)
        if case == 'short':
            record(1)
            record('complete')
            raise SystemExit(0)
    for i in range(1, 1001):
        record(i)
    record('complete')
""")
        res = subprocess.run(
            [sys.executable, "-I", "-B", "-c", HARNESS, str(SCRIPT), str(self.fixture),
             str(self.output), case, str(self.root)],
            env={**os.environ, "SYNTHETIC_SECRET": SECRET}, stdin=subprocess.DEVNULL,
            cwd=self.root, capture_output=True, timeout=12,
        )
        self.assertNotIn(SECRET.encode(), res.stdout + res.stderr)
        self.assertEqual(res.returncode, 0 if case == "success" else 1,
                         (res.stdout, res.stderr))
        summary_text = (self.output / "summary.json").read_text()
        self.assertLess(len(summary_text), 8192)
        self.assertNotIn(SECRET, summary_text)
        self.assertNotIn(str(self.root), summary_text)
        return json.loads(summary_text)["arms"]

    def assert_reaped(self, arm):
        pid = int((self.root / arm).read_text())
        with self.assertRaises(ProcessLookupError):
            os.killpg(pid, 0)

    def test_all_success_and_existing_emitter_consumes_each_arm(self):
        self.assertEqual([(a["arm"], a["status"], a["count"]) for a in self.run_case("success")],
                         [(arm, "complete", 1000) for arm in probe.ARMS])
        for arm in probe.ARMS:
            res = subprocess.run(
                [sys.executable, "-I", "-B", str(EMITTER), str(self.output / arm)],
                cwd=self.root, stdin=subprocess.DEVNULL, capture_output=True, timeout=3,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn(b'receipt.json |   "exit": 0', res.stdout)
            log = (self.output / arm / "product-tests.log").read_bytes()
            self.assertNotIn(SECRET.encode(), log)
            self.assertEqual(json.loads(log)["count"], 1000)

    def test_short_success_exit_is_not_a_completed_arm(self):
        arms = self.run_case("short")
        self.assertNotEqual(arms[0]["status"], "complete")
        self.assertEqual([a["status"] for a in arms[1:]], ["complete", "complete"])

    def test_failure_keeps_later_results_and_count(self):
        arms = self.run_case("failed")
        self.assertEqual([(a["status"], a["count"]) for a in arms],
                         [("worker-failed", 1), ("complete", 1000), ("complete", 1000)])

    def test_arm_timeout_cleans_and_continues(self):
        arms = self.run_case("timeout")
        self.assertEqual((arms[0]["status"], arms[0]["count"]), ("timeout", 1))
        self.assertEqual([a["status"] for a in arms[1:]], ["complete", "complete"])
        self.assert_reaped("no-tcl")

    def test_interrupt_and_global_alarm_clean_and_stop(self):
        for case, reason in (("signal-int", "signal"), ("signal-term", "signal"),
                             ("global", "global-budget")):
            with self.subTest(case=case):
                self.output = self.root / case
                arms = self.run_case(case)
                self.assertEqual((arms[0]["status"], arms[0]["cleanup"]), (reason, "clean"))
                for arm in arms[1:]:
                    self.assertEqual((arm["status"], arm["reason"]), ("not-run", reason))
                if case == "global":
                    receipt = json.loads((self.output / "no-tcl" / "receipt.json").read_text())
                    self.assertEqual(receipt["reason"], f"TimeoutError (errno {probe.errno.ETIMEDOUT})")
                self.assert_reaped("no-tcl")

    def test_failed_sampler_cleanup_stops_even_after_fallback_reap(self):
        arms = self.run_case("sample-cleanup")
        self.assertEqual(arms[0]["cleanup"], "uncertain")
        self.assertTrue(all(a["reason"] == "cleanup-uncertain" for a in arms[1:]))
        self.assert_reaped("no-tcl")
        self.assert_reaped("sample")

    def test_rejects_existing_links_and_override_arguments_without_echo(self):
        self.output.mkdir()
        link = self.root / "link"
        link.symlink_to(self.output)
        dangling = self.root / "dangling"
        dangling.symlink_to(self.root / "absent")
        for args in (
            ("--output", str(self.output)),
            ("--output", str(link)),
            ("--output", str(link / "nested")),
            ("--output", str(dangling)),
            ("--output", str(self.root / "fresh"), "--count", "1001"),
            ("--output", str(self.root / "fresh"), "--timeout", "3600"),
            ("--worker", "no-tcl", SECRET),
            ("--worker", SECRET),
        ):
            with self.subTest(args=args):
                res = subprocess.run(
                    [sys.executable, "-I", "-B", str(SCRIPT), *args],
                    env={"VENV_PYTHON": sys.executable}, cwd=self.root,
                    stdin=subprocess.DEVNULL, capture_output=True, timeout=3,
                )
                self.assertEqual(res.returncode, 1)
                self.assertNotIn(SECRET.encode(), res.stdout + res.stderr)
                self.assertNotIn(str(self.root).encode(), res.stdout + res.stderr)
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertFalse(dangling.exists())
        self.assertFalse((self.root / "fresh").exists())
        self.assertFalse((self.root / probe.PROGRESS).exists())


if __name__ == "__main__":
    unittest.main()
