"""Supervise the CI test group and preserve bounded, stack-only diagnostics."""
import argparse
import errno
import json
import os
from pathlib import Path
import re
import resource
import select
import signal
import subprocess
import sys
import time

MAX_OUTPUT = 20 * 1024 * 1024
MAX_STACK = 2 * 1024 * 1024
MAX_PIDS = 6
SAMPLE_TIMEOUT = 15
POLL = 0.05
PS = "/bin/ps"
SAMPLE = "/usr/bin/sample"


class DiagnosticError(Exception):
    pass


def group_members(inventory, pgid):
    members = []
    for line in inventory.splitlines():
        fields = line.split(None, 3)
        if len(fields) != 4:
            continue
        try:
            pid, ppid, group = map(int, fields[:3])
        except ValueError:
            continue
        if group == pgid and pid > 0:
            basename = re.sub(r"[^a-zA-Z0-9_.+ -]", "?", os.path.basename(fields[3]))
            members.append((pid, ppid, group, basename[:80]))
    return sorted(members, key=lambda row: (row[0] != pgid, row[0]))


def ps(*args):
    return subprocess.run(
        [PS, *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, errors="replace", timeout=2,
        env={"LC_ALL": "C"},
    )


def stack_only(raw, output):
    with raw.open("rb") as stream:
        data = stream.read(MAX_STACK + 1)
    if len(data) > MAX_STACK:
        raise DiagnosticError("sample exceeded stack limit")
    _, marker, graph = data.partition(b"\nCall graph:\n")
    if not marker:
        raise DiagnosticError("sample did not contain a call graph")
    graph = re.split(
        rb"\n(?:Total number in stack|Sort by top of stack|Binary Images)\b",
        graph, maxsplit=1,
    )[0].strip(b"\n")
    if not graph.strip():
        raise DiagnosticError("sample contained an empty call graph")
    # Keep symbols and locations; remove absolute directory prefixes in frames.
    graph = re.sub(rb"(?<=\()/[^\r\n)]*/", b"", graph)
    with output.open("xb") as stream:
        stream.write(b"Call graph:\n" + graph + b"\n")


def stop_sample(proc):
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=2)


class SampleRound:
    def __init__(self, output, pgid):
        self.output = output
        self.pgid = pgid
        self.pending = []
        self.active = None
        self.receipt = {"status": "not reached", "samples": []}

    def start(self):
        self.receipt["status"] = "started"
        try:
            result = ps("-axww", "-o", "pid=,ppid=,pgid=,comm=")
            if result.returncode:
                raise DiagnosticError("process inventory failed")
            members = group_members(result.stdout, self.pgid)
            self.pending = [row[0] for row in members[:MAX_PIDS]]
            with (self.output / "process-group.txt").open("x") as stream:
                stream.write("PID PPID PGID BASENAME\n")
                for row in members[:MAX_PIDS]:
                    stream.write("%d %d %d %s\n" % row)
            self.receipt["matched"] = len(members)
        except (OSError, subprocess.TimeoutExpired, DiagnosticError) as exc:
            self.receipt["status"] = error_label(exc)

    def record(self, pid, status):
        self.receipt["samples"].append({"pid": pid, "status": status})

    def tick(self):
        if self.active:
            pid, proc, raw, deadline = self.active
            code = proc.poll()
            if code is None and time.monotonic() < deadline:
                return
            try:
                if code is None:
                    stop_sample(proc)
                    self.record(pid, "sample timed out")
                elif code:
                    self.record(pid, f"sample exit {code} (tool or permission error)")
                else:
                    stack_only(raw, self.output / f"stack-{pid}.txt")
                    self.record(pid, "saved")
            except (OSError, subprocess.TimeoutExpired, DiagnosticError) as exc:
                self.record(pid, error_label(exc))
            finally:
                if proc.poll() is not None:
                    self.active = None
                raw.unlink(missing_ok=True)
            return
        if not self.pending:
            if self.receipt["status"] == "started":
                self.receipt["status"] = "finished"
            return
        pid = self.pending.pop(0)
        try:
            # Check membership again immediately before handing a PID to sample.
            try:
                group = os.getpgid(pid)
            except ProcessLookupError:
                group = None
            if group != self.pgid:
                self.record(pid, "no longer in group")
                return
            raw = self.output / f".sample-{pid}.raw"
            started = time.monotonic()
            proc = spawn_limited(
                [SAMPLE, str(pid), "1", "1", "-file", str(raw)], MAX_STACK,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={"LC_ALL": "C"},
            )
            self.active = (pid, proc, raw, started + SAMPLE_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.record(pid, error_label(exc))

    def close(self):
        if self.active and self.active[1].poll() is not None:
            self.tick()
        if self.active:
            pid, proc, raw, _ = self.active
            try:
                stop_sample(proc)
                self.record(pid, "stopped at end of supervision")
            except (OSError, subprocess.TimeoutExpired) as exc:
                self.record(pid, error_label(exc))
            finally:
                raw.unlink(missing_ok=True)
        if self.receipt["status"] == "started":
            self.receipt["status"] = "interrupted" if self.pending or self.active else "finished"


def error_label(exc):
    # Omit argv, paths and native tool stderr from public evidence.
    if isinstance(exc, DiagnosticError):
        return str(exc)
    if isinstance(exc, OSError):
        return f"{type(exc).__name__} (errno {exc.errno})"
    return type(exc).__name__


def spawn_limited(argv, limit, **kwargs):
    # A file-backed writer can outrun polling. Inherit a kernel file-size ceiling
    # without running Python callbacks between fork and exec.
    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    ceiling = min([limit, *(n for n in (soft, hard) if n != resource.RLIM_INFINITY)])
    resource.setrlimit(resource.RLIMIT_FSIZE, (ceiling, hard))
    try:
        return subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, start_new_session=True, **kwargs,
        )
    finally:
        resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))


def signal_group(pgid, sig):
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return False
    return True


def stop_group(child):
    pgid = child.pid
    signal_group(pgid, signal.SIGTERM)
    for sig in (signal.SIGKILL, None):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if child.poll() is not None and not signal_group(pgid, 0):
                return True
            time.sleep(POLL)
        if sig is not None:
            signal_group(pgid, sig)
    return child.poll() is not None and not signal_group(pgid, 0)


def stream_log(reader, console_fd, batch):
    remaining = min(batch, MAX_OUTPUT - reader.tell())
    while remaining and console_fd is not None:
        chunk = reader.read(min(65536, remaining))
        if not chunk:
            break
        remaining -= len(chunk)
        view = memoryview(chunk)
        deadline = time.monotonic() + POLL
        while view:
            try:
                view = view[os.write(console_fd, view):]
            except BlockingIOError:
                if time.monotonic() >= deadline or not select.select([], [console_fd], [], POLL)[1]:
                    return None
            except BrokenPipeError:
                return None
    return console_fd


def supervise(args):
    args.output.mkdir(mode=0o700)
    log = args.output / "product-tests.log"
    interrupted = None

    def catch(signum, _frame):
        nonlocal interrupted
        if interrupted is None:
            interrupted = signum

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, catch)
    child = None
    sampler = None
    code = 1
    reason = "supervisor error"
    cleanup = None
    console_fd = sys.stdout.fileno()
    console = console_fd
    console_offset = 0
    blocking = os.get_blocking(console_fd)

    try:
        # stdin is closed to the command. No child pipe is read here.
        os.set_blocking(console_fd, False)
        with log.open("xb") as writer, log.open("rb", buffering=0) as reader:
            started = time.monotonic()
            child = spawn_limited(
                args.command, MAX_OUTPUT, stdout=writer, stderr=subprocess.STDOUT,
            )
            sampler = SampleRound(args.output, child.pid)
            while True:
                if interrupted is not None:
                    code, reason = 128 + interrupted, "signal"
                    break
                elapsed = time.monotonic() - started
                if elapsed >= args.timeout:
                    code, reason = 124, "timeout"
                    break
                if os.fstat(reader.fileno()).st_size >= MAX_OUTPUT:
                    code, reason = 125, "output limit reached"
                    break
                console = stream_log(reader, console, 256 * 1024)
                console_offset = reader.tell()
                rc = child.poll()
                if rc is not None:
                    # Check again after observing exit: the last write may have
                    # happened after the first size check.
                    if os.fstat(reader.fileno()).st_size >= MAX_OUTPUT:
                        code, reason = 125, "output limit reached"
                    else:
                        code = rc if rc >= 0 else 128 - rc
                        reason = "child exit"
                        if signal_group(child.pid, 0):
                            code, reason = code or 1, "child exited with group members remaining"
                    break
                if elapsed >= args.sample_after:
                    if sampler.receipt["status"] == "not reached":
                        sampler.start()
                    else:
                        sampler.tick()
                time.sleep(POLL)
    except Exception as exc:
        code, reason = code or 1, error_label(exc)
    finally:
        try:
            if child is not None and (reason != "child exit" or code or interrupted is not None):
                cleanup = stop_group(child)
        except Exception as exc:
            cleanup = error_label(exc)
        try:
            if sampler is not None:
                sampler.close()
            if console is not None and log.exists():
                with log.open("rb", buffering=0) as reader:
                    reader.seek(console_offset)
                    console = stream_log(reader, console, MAX_OUTPUT)
        except Exception as exc:
            code, reason = code or 1, error_label(exc)
        finally:
            os.set_blocking(console_fd, blocking)

    if interrupted is not None:
        code = code or 128 + interrupted
        if reason == "child exit":
            reason = "signal"
    receipt = {
        "exit": code, "reason": reason, "signal": interrupted,
        "child_exit": child.returncode if child is not None else None,
        "group_cleanup": cleanup,
        "console_mirrored": console is not None,
        "native": sampler.receipt if sampler is not None else {"status": "not reached"},
    }
    (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("native diagnostic: " + json.dumps(receipt, separators=(",", ":")), file=sys.stderr)
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--sample-after", type=float, required=True)
    try:
        split = sys.argv.index("--")
    except ValueError:
        parser.error("expected -- COMMAND")
    args = parser.parse_args(sys.argv[1:split])
    args.command = sys.argv[split + 1:]
    if not args.command or not (0 < args.timeout <= 540 and 0 <= args.sample_after <= args.timeout):
        parser.error("expected COMMAND, 0 < timeout <= 540 and 0 <= sample-after <= timeout")
    os.umask(0o077)
    try:
        return supervise(args)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            print("native diagnostic: output must be a new directory", file=sys.stderr)
        else:
            print("native diagnostic: " + error_label(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
