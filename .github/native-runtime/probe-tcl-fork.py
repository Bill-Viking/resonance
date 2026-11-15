"""Headless differential probe on the already verified source-built venv."""
import errno
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import sysconfig
import tempfile
import time
from types import SimpleNamespace

ARMS = ("no-tcl", "ephemeral-tcl", "retained-tcl")
COUNT = 1000
ARM_SECONDS = 35
SAMPLE_AFTER = 5
OVERALL_SECONDS = 120
CLEANUP_SECONDS = 9
# Leave two extra seconds for the supervisor's bounded process inventory call.
ALARM_SECONDS = OVERALL_SECONDS - CLEANUP_SECONDS - 2
SCRIPT = Path(__file__).resolve()
WORKER_SCRIPT = SCRIPT
COMMAND = "/usr/bin/true"
PROGRESS = "observations"
MAX_PROGRESS = 8192
CLEAN_ENV = {"LC_ALL": "C"}
FEATURES = (
    "HAVE_FORK", "HAVE_VFORK", "HAVE_POSIX_SPAWN", "HAVE_POSIX_SPAWNP",
    "HAVE_POSIX_SPAWN_FILE_ACTIONS_ADDCLOSEFROM_NP",
)
WORKER_ERRORS = {"tcl-error", "timeout", "command-error", "worker-error"}
VERSION = re.compile(r"[0-9]{1,3}(?:\.[0-9]{1,3}){1,2}")
RETAINED_TCL = None


def recipe(filename):
    spec = importlib.util.spec_from_file_location(filename, SCRIPT.with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def numeric_version(value):
    return value if VERSION.fullmatch(value) else None


def runtime():
    machine = platform.machine()
    return {
        "python": list(sys.version_info[:3]),
        "architecture": machine if machine in {"x86_64", "arm64", "aarch64"} else "other",
        "macos": numeric_version(platform.mac_ver()[0]),
        "isolated": bool(sys.flags.isolated),
        "no_user_site": bool(sys.flags.no_user_site),
        "posix_spawn": getattr(subprocess, "_USE_POSIX_SPAWN", None),
        "posix_spawn_closefrom": getattr(subprocess, "_HAVE_POSIX_SPAWN_CLOSEFROM", None),
        "sysconfig": {key: bool(sysconfig.get_config_var(key)) for key in FEATURES},
    }


def worker(arm):
    global RETAINED_TCL
    cwd = Path.cwd()
    with (cwd / PROGRESS).open("xb", buffering=0) as stream:
        def record(value):
            stream.write((json.dumps(value, separators=(",", ":")) + "\n").encode("ascii"))

        record(runtime())
        patch = None
        if arm != "no-tcl":
            try:
                import tkinter
                if arm == "retained-tcl":
                    RETAINED_TCL = tkinter.Tcl()
                    patch = numeric_version(str(RETAINED_TCL.call("info", "patchlevel")))
                else:
                    patch = numeric_version(str(tkinter.Tcl().call("info", "patchlevel")))
                if patch is None:
                    raise ValueError
            except Exception:
                record("tcl-error")
                return 1
        record({"tcl_patch": patch})
        try:
            for count in range(1, COUNT + 1):
                subprocess.run(
                    [COMMAND], cwd=cwd, env=CLEAN_ENV, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, close_fds=True,
                    check=True, timeout=2,
                )
                record(count)
        except subprocess.TimeoutExpired:
            record("timeout")
        except (OSError, subprocess.CalledProcessError):
            record("command-error")
        except Exception:
            record("worker-error")
        else:
            record("complete")
            return 0
        return 1


def require(condition):
    if not condition:
        raise ValueError


def valid_runtime(value):
    require(isinstance(value, dict) and set(value) == {
        "python", "architecture", "macos", "isolated", "no_user_site",
        "posix_spawn", "posix_spawn_closefrom", "sysconfig",
    })
    py = value["python"]
    require(isinstance(py, list) and len(py) == 3
            and all(type(n) is int and 0 <= n <= 999 for n in py))
    require(value["architecture"] in ("x86_64", "arm64", "aarch64", "other"))
    mac = value["macos"]
    require(mac is None or isinstance(mac, str) and VERSION.fullmatch(mac))
    require(all(type(value[key]) is bool for key in ("isolated", "no_user_site")))
    require(all(value[key] is None or type(value[key]) is bool
                for key in ("posix_spawn", "posix_spawn_closefrom")))
    config = value["sysconfig"]
    require(isinstance(config, dict) and set(config) == set(FEATURES)
            and all(type(flag) is bool for flag in config.values()))


def observations(path, arm, evidence):
    """Read bounded, ordinary files; never mirror worker stdout or stderr."""
    result = {"count": 0, "worker_status": "no-observations"}
    try:
        fd = evidence.open_directory(str(path))
        try:
            data = evidence.read_file(fd, PROGRESS, MAX_PROGRESS)
        finally:
            os.close(fd)
        if not data:
            return result
        # A killed writer can leave an incomplete final line. Only count full lines.
        lines = data.split(b"\n")[:-1]
        if not lines:
            return result
        rt, *updates = [json.loads(line, object_pairs_hook=evidence.unique_object) for line in lines]
        valid_runtime(rt)
        result["runtime"] = rt
        result["worker_status"] = "incomplete"
        if updates == ["tcl-error"] and arm != "no-tcl":
            result["worker_status"] = "tcl-error"
        elif updates:
            patch, *updates = updates
            require(isinstance(patch, dict) and set(patch) == {"tcl_patch"})
            version = patch["tcl_patch"]
            require(version is None if arm == "no-tcl"
                    else isinstance(version, str) and VERSION.fullmatch(version))
            result.update(patch)
            for index, update in enumerate(updates, 1):
                if index == len(updates) and isinstance(update, str):
                    require(update in WORKER_ERRORS - {"tcl-error"}
                            or update == "complete" and result["count"] == COUNT)
                    result["worker_status"] = update
                else:
                    require(type(update) is int and update == index <= COUNT)
                    result["count"] = update
    except FileNotFoundError:
        pass
    except (OSError, ValueError, RecursionError):
        return {"count": 0, "worker_status": "invalid-observations"}
    return result


class Budget:
    def __init__(self):
        self.started = time.monotonic()
        self.expired = False
        self.interrupted = None
        self.in_poll = False

    def alarm(self, _signum, _frame):
        self.expired = True
        # An exception during Popen can lose the child handle. During cleanup it
        # can strand a worker or sample. Defer to the next supervised poll there.
        if self.in_poll:
            self.raise_timeout()

    @staticmethod
    def raise_timeout():
        raise TimeoutError(errno.ETIMEDOUT, "probe deadline")

    def interrupt(self, signum, _frame):
        if self.interrupted is None:
            self.interrupted = signum

    def poll_sleep(self, delay):
        try:
            self.in_poll = True
            if self.expired:
                self.raise_timeout()
            time.sleep(delay)
        finally:
            self.in_poll = False

    def arm_seconds(self):
        available = self.started + ALARM_SECONDS - time.monotonic()
        return min(ARM_SECONDS, available)

    def stop_reason(self):
        if self.interrupted is not None:
            return "signal"
        if self.expired or self.arm_seconds() <= 0:
            return "global-budget"
        return None


def supervise_arm(output, cwd, arm, python, budget, evidence):
    # Each imported module has private globals. Adapt only its process ownership,
    # console inputs and signal/poll hooks; the supervisor source stays unchanged.
    diag = recipe("diagnose-suite.py")
    stop_group, stop_sample = diag.stop_group, diag.stop_sample
    managed = []
    sampler_cleanup_failed = False
    launch_cancelled = None

    def popen(argv, **kwargs):
        nonlocal launch_cancelled
        if not managed:
            launch_cancelled = budget.stop_reason()
            if launch_cancelled == "global-budget":
                budget.raise_timeout()
            if launch_cancelled == "signal":
                raise InterruptedError(errno.EINTR, "probe interrupted")
            kwargs.update(cwd=cwd, env=CLEAN_ENV, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)
        # Register before spawn_limited restores the parent's file-size limit.
        child = subprocess.Popen(argv, **kwargs)
        managed.append(child)
        return child

    def cleanup_sample(child):
        nonlocal sampler_cleanup_failed
        try:
            stop_sample(child)
        except (OSError, subprocess.TimeoutExpired):
            sampler_cleanup_failed = True
            raise

    def install(sig, callback):
        def trap(signum, frame):
            budget.interrupt(signum, frame)
            callback(signum, frame)
        return signal.signal(sig, trap)

    def cleanup_group(child):
        # The supervisor also sleeps while reaping a group. Do not raise there.
        diag.time.sleep = time.sleep
        return stop_group(child)

    diag.subprocess = SimpleNamespace(Popen=popen, run=subprocess.run, DEVNULL=subprocess.DEVNULL,
                                      PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT,
                                      TimeoutExpired=subprocess.TimeoutExpired)
    diag.stop_group = cleanup_group
    diag.stop_sample = cleanup_sample
    diag.time = SimpleNamespace(monotonic=time.monotonic, sleep=budget.poll_sleep)
    diag.signal = SimpleNamespace(SIGINT=signal.SIGINT, SIGTERM=signal.SIGTERM,
                                  SIGKILL=signal.SIGKILL, signal=install)

    result = {"arm": arm, "status": "supervisor-error"}
    cleanup = False
    timeout = budget.arm_seconds()
    reason = budget.stop_reason()
    if reason or timeout <= 0:
        return {"arm": arm, "status": "not-run", "count": 0, "reason": reason or "global-budget"}
    try:
        code = diag.supervise(SimpleNamespace(
            output=output, timeout=timeout, sample_after=SAMPLE_AFTER,
            command=[python, "-I", "-B", str(WORKER_SCRIPT), "--worker", arm],
        ))
        directory = evidence.open_directory(str(output))
        try:
            data = evidence.read_file(directory, "receipt.json", evidence.MAX_RECEIPT)
            evidence.receipt_fields(data)
            receipt = json.loads(data)
        finally:
            os.close(directory)
        reason = receipt["reason"]
        child_exited = receipt["child_exit"] == 0 and reason == "child exit"
        cleanup = (receipt["group_cleanup"] is True
                   or receipt["group_cleanup"] is None
                   and (child_exited and code == 0 or not managed and receipt["child_exit"] is None))
        require(code == receipt["exit"])
        if receipt["signal"] is not None or budget.interrupted is not None:
            result["status"] = "signal"
        elif budget.expired or reason == f"TimeoutError (errno {errno.ETIMEDOUT})":
            result["status"] = "global-budget"
        elif reason == "timeout":
            result["status"] = "timeout"
        elif reason == "child exit":
            result["status"] = "candidate-complete" if code == 0 else "worker-failed"
    except (OSError, ValueError, RuntimeError):
        # No worker or sample could exist before the first registered Popen.
        cleanup = not managed
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, budget.interrupt)
        cleanup = verify_cleanup(diag, managed) and cleanup and not sampler_cleanup_failed

    if launch_cancelled:
        return {"arm": arm, "status": "not-run", "count": 0, "reason": launch_cancelled}
    result.update(observations(cwd, arm, evidence))
    result["cleanup"] = "clean" if cleanup else "uncertain"
    if result["status"] == "candidate-complete":
        result["status"] = (
            "complete" if cleanup and result["worker_status"] == "complete" and result["count"] == COUNT
            else "incomplete"
        )
    # The supervisor's worker log was kept empty while it ran; only write the
    # normalized observation after the raw worker output has been discarded.
    try:
        with (output / "product-tests.log").open("r+", encoding="ascii") as stream:
            stream.write(json.dumps(result, separators=(",", ":")) + "\n")
    except OSError:
        result["status"] = "supervisor-error"
    # Even after a successful fallback reap, an unexpected live group is failure.
    return result


def verify_cleanup(diag, managed):
    """Independently verify worker and sample groups; bounded last-chance reap."""
    def live(child):
        return child.poll() is None or diag.signal_group(child.pid, 0)

    pending = []
    uncertain = False
    for child in managed:
        try:
            if live(child):
                pending.append(child)
                uncertain = True
        except OSError:
            pending.append(child)
            uncertain = True
    for child in pending:
        try:
            diag.signal_group(child.pid, signal.SIGKILL)
        except OSError:
            pass
    deadline = time.monotonic() + 1
    while pending and time.monotonic() < deadline:
        try:
            pending = [child for child in pending if live(child)]
        except OSError:
            break
        if pending:
            time.sleep(diag.POLL)
    return not uncertain


def coordinate(output, python):
    budget = Budget()
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM)}
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    results = []
    stopped = None
    try:
        signal.signal(signal.SIGALRM, budget.alarm)
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, budget.interrupt)
        delay = budget.started + ALARM_SECONDS - time.monotonic()
        if delay > 0:
            signal.setitimer(signal.ITIMER_REAL, delay)
        else:
            budget.expired = True
        evidence = recipe("emit-evidence.py")
        if not output.is_absolute() or output.name in {"", ".", ".."}:
            raise ValueError
        parent = evidence.open_directory(str(output.parent))
        try:
            os.mkdir(output.name, 0o700, dir_fd=parent)
        finally:
            os.close(parent)
        for arm in ARMS:
            stopped = stopped or budget.stop_reason()
            if stopped:
                results.append({"arm": arm, "status": "not-run", "count": 0, "reason": stopped})
                continue
            with tempfile.TemporaryDirectory(prefix="synthetic-", dir=output.parent) as scratch:
                result = supervise_arm(output / arm, Path(scratch), arm, python, budget, evidence)
                results.append(result)
                if result["status"] == "not-run":
                    stopped = result["reason"]
                elif result["cleanup"] != "clean":
                    stopped = "cleanup-uncertain"
        stopped = budget.stop_reason() or stopped
        ok = stopped is None and all(row["status"] == "complete" for row in results)
        summary = {"status": "complete" if ok else "failed", "arms": results, "stop": stopped}
        with (output / "summary.json").open("x", encoding="ascii") as stream:
            stream.write(json.dumps(summary, indent=2) + "\n")
        print("Tcl fork probe: " + json.dumps(summary, separators=(",", ":")))
        return 0 if ok and budget.stop_reason() is None else 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for sig, handler in saved.items():
            signal.signal(sig, handler)
        signal.setitimer(signal.ITIMER_REAL, *old_timer)


def main():
    args = sys.argv[1:]
    os.umask(0o077)
    try:
        if len(args) == 2 and args[0] == "--worker" and args[1] in ARMS:
            return worker(args[1])
        if len(args) == 2 and args[0] == "--output":
            python = os.environ.get("VENV_PYTHON", "")
            if os.path.isabs(python) and os.access(python, os.X_OK):
                return coordinate(Path(args[1]), python)
    except (OSError, ValueError, RuntimeError):
        pass
    print("Tcl fork probe: invalid configuration or probe error", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
