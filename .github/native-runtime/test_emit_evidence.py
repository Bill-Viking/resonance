"""Headless inline evidence tests using only temporary synthetic files."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("emit-evidence.py").resolve()
PREFIX = b"native evidence "
FAILURE = b"native evidence: invalid or unreadable\n"
HEADER = b"PID PPID PGID BASENAME\n"
ROWS = b"41 0 41 demo-worker\n42 41 41 demo + side?.py\n"


class EmitEvidenceTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name).resolve()
        self.output = self.scratch / "native-stack-diagnostic"
        self.output.mkdir()
        self.receipt = self.output / "receipt.json"
        self.process = self.output / "process-group.txt"
        self.stack = self.output / "stack-41.txt"
        self.native = {
            "status": "finished", "matched": 2,
            "samples": [{"pid": 41, "status": "saved"},
                        {"pid": 42, "status": "no longer in group"}],
        }
        self.write_receipt()
        self.process.write_bytes(HEADER + ROWS)
        self.stack.write_bytes(b"Call graph:\n  demo_frame (module.py:9)\n")

    def write_receipt(self, **changes):
        receipt = {
            "exit": 124, "reason": "timeout", "signal": None, "child_exit": -9,
            "group_cleanup": True, "console_mirrored": True, "native": self.native,
        }
        receipt.update(changes)
        self.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="ascii")

    def run_helper(self, *args):
        return subprocess.run(
            [sys.executable, "-I", "-B", str(SCRIPT), *args],
            cwd=self.scratch, stdin=subprocess.DEVNULL, capture_output=True, timeout=5,
        )

    def assert_refused(self, *args):
        result = self.run_helper(*(args or (str(self.output),)))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, FAILURE)

    def emitted(self):
        result = self.run_helper(str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assertTrue(all(line.startswith(PREFIX) for line in result.stdout.splitlines()))
        return result.stdout

    def assert_file(self, out, path, truncated=False):
        start = PREFIX + path.name.encode()
        body_prefix = start + b" | "
        body = b"".join(line for line in out.splitlines(keepends=True) if line.startswith(body_prefix))
        self.assertTrue(body)
        original = path.read_bytes()
        status = "yes" if truncated else "no"
        self.assertIn(start + (
            f": sha256={hashlib.sha256(original).hexdigest()} original_bytes={len(original)} "
            f"output_bytes={len(body)} truncated={status}\n"
        ).encode(), out)
        return body

    def test_valid_files_and_metadata(self):
        out = self.emitted()
        for file in (self.receipt, self.process, self.stack):
            self.assert_file(out, file)
        self.assertIn(b'receipt.json |   "reason": "timeout"', out)
        self.assertIn(b"process-group.txt | " + HEADER, out)
        self.assertIn(b"process-group.txt | 42 41 41 demo + side?.py", out)
        self.assertIn(b"stack-41.txt |   demo_frame (module.py:9)", out)

    def test_stack_scrub_and_workflow_markers(self):
        self.stack.write_bytes(
            b"Call graph:\n  frame (/synthetic/private/module.py:9)\n"
            b"\r::notice::demo\x1b\t\x00\n  ##[warning]demo\n"
            b"frame\xc2\x85\xe2\x80\xa8\xe2\x80\xae\n"
            b"Binary Images:\nNOT_FOR_OUTPUT\n"
        )
        out = self.emitted()
        self.assertIn(b"stack-41.txt | ::notice::demo\n", out)
        self.assertIn(b"stack-41.txt |   ##\\[warning]demo\n", out)
        self.assertIn(b"stack-41.txt |   frame (module.py:9)\n", out)
        for value in (b"##[", b"Binary Images", b"NOT_FOR_OUTPUT", b"/synthetic",
                      b"\x1b", b"\x00", b"\t", b"\r", b"\xc2\x85",
                      b"\xe2\x80\xa8", b"\xe2\x80\xae"):
            self.assertNotIn(value, out)

    def test_raw_unsaved_and_outside_are_ignored(self):
        outside = self.scratch / "stack-99.txt"
        outside.write_bytes(b"Call graph:\nNOT_FOR_OUTPUT\n")
        for ignored in ("stack-42.txt", "product-tests.log"):
            (self.output / ignored).symlink_to(outside)
        (self.output / ".sample-41.raw").write_bytes(b"NOT_FOR_OUTPUT")
        out = self.emitted()
        for value in (b"NOT_FOR_OUTPUT", b"stack-42.txt", b"stack-99.txt", b".sample-", b"product-tests.log"):
            self.assertNotIn(value, out)

    def test_allowed_file_symlinks_are_refused(self):
        for path in (self.receipt, self.process, self.stack):
            with self.subTest(path=path.name):
                outside = self.scratch / path.name
                path.rename(outside)
                path.symlink_to(outside)
                self.assert_refused()
                path.unlink()
                outside.rename(path)

    def test_directory_symlink_and_symlink_ancestor_are_refused(self):
        link = self.scratch / "linked-evidence"
        link.symlink_to(self.output, target_is_directory=True)
        self.assert_refused(str(link) + "/")
        link.unlink()
        link.symlink_to(self.scratch, target_is_directory=True)
        self.assert_refused(str(link / self.output.name))

    def test_nonregular_and_hardlinked_files_are_refused(self):
        self.stack.unlink()
        os.mkfifo(self.stack)
        self.assert_refused()
        self.stack.unlink()
        outside = self.scratch / "outside.txt"
        outside.write_bytes(b"Call graph:\nNOT_FOR_OUTPUT\n")
        os.link(outside, self.stack)
        self.assert_refused()

    def test_bad_arguments_and_unexpected_names_do_not_echo_input(self):
        self.assert_refused("NOT_FOR_OUTPUT")
        self.assert_refused(str(self.output), "NOT_FOR_OUTPUT")
        for name in ("NOT_FOR_OUTPUT.txt", "stack-01.txt", "stack-0.txt"):
            unexpected = self.output / name
            unexpected.write_bytes(b"NOT_FOR_OUTPUT")
            self.assert_refused()
            unexpected.unlink()

    def test_malformed_receipt_is_refused(self):
        cases = [
            {"reason": "NOT_FOR_OUTPUT"},
            {"exit": True},
            {"console_mirrored": 1},
            {"native": {"status": "NOT_FOR_OUTPUT", "samples": []}},
            {"native": {"status": "finished", "matched": 2, "samples": [
                {"pid": 41, "status": "saved"}, {"pid": 41, "status": "no longer in group"},
            ]}},
            {"native": {"status": "finished", "matched": 2, "samples": [
                {"pid": 41, "status": "saved", "filename": "../NOT_FOR_OUTPUT"},
            ]}},
            {"native": {"status": "finished", "matched": 7, "samples": [
                {"pid": pid, "status": "saved"} for pid in range(41, 48)
            ]}},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                self.write_receipt(**changes)
                self.assert_refused()
        self.receipt.write_bytes(b'{"exit": 0, "exit": 1, "reason": "NOT_FOR_OUTPUT"}\n')
        self.assert_refused()
        self.receipt.write_bytes(b"[" * 4000 + b"NOT_FOR_OUTPUT")
        self.assert_refused()

    def test_malformed_process_list_is_refused(self):
        for data in (
            b"NOT_FOR_OUTPUT\n" + ROWS, HEADER + b"41 0 41 /NOT_FOR_OUTPUT\n",
            HEADER + b"41 0 41 demo\n41 0 41 demo\n",
            HEADER + b"41 0 41 demo\n43 0 41 demo\n",
            HEADER + b"41 0 41 demo\n42 41 53 demo\n",
            HEADER + b"".join(f"{pid} 0 41 demo\n".encode() for pid in range(41, 48)),
        ):
            with self.subTest(data=data):
                self.process.write_bytes(data)
                self.assert_refused()

    def test_oversized_metadata_is_refused(self):
        self.receipt.write_bytes(self.receipt.read_bytes() + b" " * (16 * 1024))
        self.assert_refused()
        self.write_receipt()
        self.process.write_bytes(HEADER + ROWS + b" " * 1024)
        self.assert_refused()

    def test_missing_or_invalid_saved_stack_is_refused(self):
        for data in (b"NOT_FOR_OUTPUT\nCall graph:\nframe\n",
                     b"Call graph:\n\x00\n", b"Call graph:\nBinary Images:\nNOT_FOR_OUTPUT\n"):
            self.stack.write_bytes(data)
            self.assert_refused()
        self.stack.unlink()
        self.assert_refused()

    def test_stack_read_and_output_caps(self):
        ceiling = 2 * 1024 * 1024
        header = b"Call graph:\n"
        self.stack.write_bytes(header + b"x" * (ceiling - len(header) - 1) + b"\n")
        body = self.assert_file(self.emitted(), self.stack, truncated=True)
        self.assertEqual(len(body), 256 * 1024)
        self.assertTrue(body.endswith(b"\n"))
        with self.stack.open("ab") as stream:
            stream.write(b"x")
        self.assert_refused()

    def test_stack_output_limit_counts_prefixes_and_keeps_utf8(self):
        self.stack.write_bytes(("Call graph:\n" + "\u2603\n" * 9000).encode())
        body = self.assert_file(self.emitted(), self.stack, truncated=True)
        self.assertLessEqual(len(body), 256 * 1024)
        self.assertIn("\u2603", body.decode("utf-8"))

    def test_not_reached_and_inventory_failure_can_lack_process_list(self):
        self.process.unlink()
        self.stack.unlink()
        for native in (
            {"status": "not reached"}, {"status": "not reached", "samples": []},
            {"status": "process inventory failed", "samples": []},
        ):
            self.write_receipt(native=native)
            out = self.emitted()
            self.assert_file(out, self.receipt)
            self.assertNotIn(b"process-group.txt", out)
        self.write_receipt()
        self.assert_refused()

    def test_six_saved_stacks_with_more_group_members(self):
        pids = list(range(41, 47))
        self.write_receipt(native={
            "status": "finished", "matched": 9,
            "samples": [{"pid": pid, "status": "saved"} for pid in pids],
        })
        self.process.write_bytes(HEADER + b"".join(
            f"{pid} 0 41 demo\n".encode() for pid in pids
        ))
        for pid in pids:
            (self.output / f"stack-{pid}.txt").write_bytes(b"Call graph:\ndemo_frame\n")
        out = self.emitted()
        self.assertEqual(out.count(b"| Call graph:\n"), 6)

    def test_missing_directory_is_harmless_but_existing_empty_is_not(self):
        result = self.run_helper(str(self.scratch / "absent"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"native evidence: diagnostic directory absent\n")
        self.assertEqual(result.stderr, b"")
        for path in self.output.iterdir():
            path.unlink()
        self.assert_refused()


if __name__ == "__main__":
    unittest.main()
