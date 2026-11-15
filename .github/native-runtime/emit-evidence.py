"""Mirror only bounded, scrubbed supervisor evidence to the CI job log."""
import hashlib
import json
import os
import re
import stat
import sys
import unicodedata

MAX_RECEIPT = 16 * 1024
MAX_PROCESS_LIST = 1024
MAX_STACK = 2 * 1024 * 1024
MAX_STACK_OUTPUT = 256 * 1024
MAX_PIDS = 6
MAX_ID = (1 << 31) - 1
HEADER = b"Call graph:\n"
PROCESS_HEADER = b"PID PPID PGID BASENAME\n"
PID = r"[1-9][0-9]{0,9}"
STACK_NAME = re.compile(rf"stack-({PID})\.txt")
RAW_NAME = re.compile(rf"\.sample-({PID})\.raw")
PROCESS_ROW = re.compile(
    rf"({PID}) (0|{PID}) ({PID}) ([a-zA-Z0-9_.+ ?-]{{1,80}})".encode("ascii")
)
FAILURE = "native evidence: invalid or unreadable"
ABSENT = "native evidence: diagnostic directory absent"

# diagnose-suite.py emits fixed states and exception class/errno labels, never messages.
EXCEPTION_TYPES = (
    "OSError|BlockingIOError|BrokenPipeError|ChildProcessError|ConnectionError|"
    "ConnectionAbortedError|ConnectionRefusedError|ConnectionResetError|FileExistsError|"
    "FileNotFoundError|InterruptedError|IsADirectoryError|NotADirectoryError|"
    "PermissionError|ProcessLookupError|TimeoutError"
)
ERROR_LABEL = re.compile(rf"(?:{EXCEPTION_TYPES}) \(errno (?:None|-?[0-9]{{1,10}})\)")
SAMPLE_EXIT = re.compile(r"sample exit -?[1-9][0-9]{0,9} \(tool or permission error\)")
EXCEPTIONS = {
    "TimeoutExpired", "ValueError",
    "TypeError", "RuntimeError", "OverflowError", "MemoryError", "UnicodeDecodeError",
    "UnicodeEncodeError",
}
REASONS = EXCEPTIONS | {
    "supervisor error", "signal", "timeout", "output limit reached", "child exit",
    "child exited with group members remaining",
}
SAMPLE_STATES = {
    "saved", "sample timed out", "no longer in group", "stopped at end of supervision",
    "sample exceeded stack limit", "sample did not contain a call graph",
    "sample contained an empty call graph", "TimeoutExpired",
}
NATIVE_STATES = {"not reached", "started", "finished", "interrupted"}
INVENTORY_ERRORS = {"process inventory failed", "TimeoutExpired"}


class InvalidEvidence(ValueError):
    pass


def require(condition):
    if not condition:
        raise InvalidEvidence


def ordinary_open(name, dir_fd=None, *, directory=False):
    """Bind the opened object to its ordinary directory entry before any read."""
    before = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    require(kind(before.st_mode))
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= os.O_DIRECTORY
    fd = os.open(name, flags, dir_fd=dir_fd)
    try:
        after = os.fstat(fd)
        require(kind(after.st_mode))
        require((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino))
        require(directory or after.st_nlink == 1)
    except BaseException:
        os.close(fd)
        raise
    return fd


def open_directory(path):
    parts = path.rstrip("/").split("/")
    require(parts[0] == "" and len(parts) > 1)
    require(all(part not in {"", ".", ".."} for part in parts[1:]))

    # Open one component at a time so even ancestor symlinks cannot redirect reads.
    fd = ordinary_open("/", directory=True)
    for part in parts[1:]:
        try:
            next_fd = ordinary_open(part, fd, directory=True)
        finally:
            os.close(fd)
        fd = next_fd
    return fd


def read_file(directory, name, limit):
    fd = ordinary_open(name, directory)
    with os.fdopen(fd, "rb") as stream:
        require(os.fstat(stream.fileno()).st_size <= limit)
        data = stream.read(limit + 1)
        require(len(data) <= limit)
    return data


def evidence_names(directory):
    # The supervisor can leave its log, a partial unsaved stack, or a raw sample.
    # Inspect names only; ignored entries are never stat'ed or opened.
    names = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            name = entry.name
            if name not in {"receipt.json", "process-group.txt", "product-tests.log"}:
                match = STACK_NAME.fullmatch(name) or RAW_NAME.fullmatch(name)
                require(match is not None and int(match[1]) <= MAX_ID)
            names.add(name)
            require(len(names) <= 3 + 2 * MAX_PIDS)
    return names


def unique_object(pairs):
    result = dict(pairs)
    require(len(result) == len(pairs))
    return result


def integer(value, low=0, high=MAX_ID):
    return type(value) is int and low <= value <= high


def label(value, choices, *patterns):
    return isinstance(value, str) and (
        value in choices or any(pattern.fullmatch(value) for pattern in patterns)
    )


def receipt_fields(data):
    require(all(byte == 10 or 32 <= byte <= 126 for byte in data))
    receipt = json.loads(data, object_pairs_hook=unique_object)
    require(isinstance(receipt, dict) and set(receipt) == {
        "exit", "reason", "signal", "child_exit", "group_cleanup", "console_mirrored", "native",
    })
    require(integer(receipt["exit"]) and label(receipt["reason"], REASONS, ERROR_LABEL))
    require(receipt["signal"] is None or (
        integer(receipt["signal"]) and receipt["signal"] in {2, 15}
    ))
    child_exit = receipt["child_exit"]
    require(child_exit is None or integer(child_exit, -MAX_ID))
    cleanup = receipt["group_cleanup"]
    require(cleanup is None or type(cleanup) is bool or label(cleanup, EXCEPTIONS, ERROR_LABEL))
    require(type(receipt["console_mirrored"]) is bool)

    native = receipt["native"]
    require(isinstance(native, dict))
    if native == {"status": "not reached"}:
        return None, set(), []
    require(set(native) in ({"status", "samples"}, {"status", "samples", "matched"}))
    status = native["status"]
    require(label(status, NATIVE_STATES | INVENTORY_ERRORS, ERROR_LABEL))
    matched = native.get("matched")
    if "matched" in native:
        require(integer(matched) and status in NATIVE_STATES - {"not reached"})
    samples = native["samples"]
    require(isinstance(samples, list) and len(samples) <= MAX_PIDS)
    require(not samples or matched is not None and len(samples) <= matched)
    seen, saved = set(), []
    for sample in samples:
        require(isinstance(sample, dict) and set(sample) == {"pid", "status"})
        pid, status = sample["pid"], sample["status"]
        require(integer(pid, 1) and pid not in seen)
        require(label(status, SAMPLE_STATES, ERROR_LABEL, SAMPLE_EXIT))
        seen.add(pid)
        if status == "saved":
            saved.append(pid)
    return matched, seen, saved


def process_ids(data):
    require(data.startswith(PROCESS_HEADER) and data.endswith(b"\n"))
    rows = data.split(b"\n")[1:-1]
    require(len(rows) <= MAX_PIDS)
    pids, groups = set(), set()
    for row in rows:
        match = PROCESS_ROW.fullmatch(row)
        require(match is not None)
        pid, ppid, pgid = map(int, match.groups()[:3])
        require(max(pid, ppid, pgid) <= MAX_ID and pid not in pids)
        pids.add(pid)
        groups.add(pgid)
    require(len(groups) <= 1)
    return pids


def stack_text(data):
    require(data.startswith(HEADER))
    graph = data[len(HEADER):].decode("utf-8", errors="replace")
    graph = "".join(
        char for char in graph if char == "\n" or (
            (category := unicodedata.category(char))[0] != "C" and category not in {"Zl", "Zp"}
        )
    )
    # Keep the supervisor's call-graph cutoff and frame path scrub.
    graph = re.split(
        r"\n(?:Total number in stack|Sort by top of stack|Binary Images)\b",
        "\n" + graph, maxsplit=1,
    )[0].strip("\n")
    require(graph.strip())
    graph = re.sub(r"(?<=\()/[^\r\n)]*/", "", graph)
    return ("Call graph:\n" + graph + "\n").encode("utf-8")


def render(name, original, content, limit=None):
    prefix = f"native evidence {name} | ".encode("ascii")
    body = bytearray()
    truncated = False
    for line in content.removesuffix(b"\n").split(b"\n"):
        # The legacy workflow-command marker can be recognized inside a line.
        line = line.replace(b"##[", b"##\\[")
        if limit is not None and len(body) + len(prefix) + len(line) + 1 > limit:
            truncated = True
            remaining = limit - len(body) - len(prefix) - 1
            if remaining > 0:
                # The byte limit can land partway through a UTF-8 character.
                partial = line[:remaining].decode("utf-8", errors="ignore").encode("utf-8")
                body.extend(prefix + partial + b"\n")
            break
        body.extend(prefix + line + b"\n")
    metadata = (
        f"native evidence {name}: sha256={hashlib.sha256(original).hexdigest()} "
        f"original_bytes={len(original)} output_bytes={len(body)} "
        f"truncated={'yes' if truncated else 'no'}\n"
    )
    return metadata.encode("ascii") + body


def select_evidence(directory):
    names = evidence_names(directory)
    receipt = read_file(directory, "receipt.json", MAX_RECEIPT)
    matched, sampled, saved = receipt_fields(receipt)
    output = [render("receipt.json", receipt, receipt)]
    if matched is not None or "process-group.txt" in names:
        process_list = read_file(directory, "process-group.txt", MAX_PROCESS_LIST)
        pids = process_ids(process_list)
        require(matched is None or len(pids) == min(matched, MAX_PIDS))
        require(sampled <= pids)
        output.append(render("process-group.txt", process_list, process_list))
    for pid in saved:
        name = f"stack-{pid}.txt"
        stack = read_file(directory, name, MAX_STACK)
        output.append(render(name, stack, stack_text(stack), MAX_STACK_OUTPUT))
    return output


def main():
    try:
        require(len(sys.argv) == 2)
        try:
            directory = open_directory(sys.argv[1])
        except FileNotFoundError:
            print(ABSENT)
            return 0
        try:
            output = select_evidence(directory)
        finally:
            os.close(directory)
        sys.stdout.buffer.write(b"".join(output))
        sys.stdout.buffer.flush()
        return 0
    except (OSError, ValueError, RecursionError):
        print(FAILURE, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
