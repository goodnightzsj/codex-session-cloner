"""Concurrency, atomic-write, and cross-platform path tests.

Covers the safety invariants that previously had no test coverage:
  * ``atomic_write`` rolls back its tempfile and leaves the target untouched
    when the body raises.
  * ``atomic_write(lock_path=...)`` serialises concurrent writers so neither
    side observes a partial frame from the other.
  * ``file_lock`` provides mutual exclusion across threads on POSIX.
  * ``ensure_path_within_dir`` accepts case-variant paths on case-insensitive
    filesystems (Windows / macOS) and rejects genuine escapes.
  * ``lock_path_for`` deterministically maps any data file to a single shared
    lock-file path so independent writers serialise on the same lock.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


from ai_cli_kit.codex.errors import ToolkitError  # noqa: E402
from ai_cli_kit.codex.support import (  # noqa: E402
    atomic_write,
    ensure_path_within_dir,
    file_lock,
    lock_path_for,
)


class AtomicWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_dir = Path(self.tmp.name)

    def test_replaces_target_on_success(self) -> None:
        target = self.tmp_dir / "out.txt"
        target.write_text("old", encoding="utf-8")
        with atomic_write(target) as fh:
            fh.write("new")
        self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_rolls_back_temp_when_body_raises(self) -> None:
        target = self.tmp_dir / "out.txt"
        target.write_text("preserved", encoding="utf-8")

        with self.assertRaises(RuntimeError):
            with atomic_write(target) as fh:
                fh.write("partial")
                raise RuntimeError("boom")

        # Target unchanged
        self.assertEqual(target.read_text(encoding="utf-8"), "preserved")
        # No leftover .tmp files in the directory
        leftovers = sorted(p.name for p in self.tmp_dir.iterdir() if p.suffix == ".tmp")
        self.assertEqual(leftovers, [], f"unexpected tempfile leftovers: {leftovers}")

    def test_lf_only_on_all_platforms(self) -> None:
        target = self.tmp_dir / "out.jsonl"
        with atomic_write(target) as fh:
            fh.write("line1\nline2\n")
        # newline="" prevents Python from translating \n -> \r\n on Windows.
        self.assertEqual(target.read_bytes(), b"line1\nline2\n")

    def test_lock_path_serialises_concurrent_writers(self) -> None:
        target = self.tmp_dir / "shared.jsonl"
        target.write_text("", encoding="utf-8")
        lock = lock_path_for(target)

        # Two threads each take the lock and rewrite the file with their own
        # marker line. With the lock, each rewrite is atomic; the final file
        # must contain exactly ONE complete writer's content (whichever ran
        # last) — never an interleaved/corrupted blend.
        results: list[str] = []
        barrier = threading.Barrier(2)

        def writer(marker: str) -> None:
            barrier.wait()
            with atomic_write(target, lock_path=lock) as fh:
                fh.write(f"{marker}-line-1\n")
                # tiny pause inside critical section makes interleaving observable
                # if the lock is broken
                time.sleep(0.01)
                fh.write(f"{marker}-line-2\n")
            results.append(marker)

        t1 = threading.Thread(target=writer, args=("alpha",))
        t2 = threading.Thread(target=writer, args=("bravo",))
        t1.start(); t2.start()
        t1.join(); t2.join()

        # Final content is exactly two lines from one writer (no mixing)
        final = target.read_text(encoding="utf-8")
        lines = final.splitlines()
        self.assertEqual(len(lines), 2, f"expected 2 lines, got {lines!r}")
        winner_marker = lines[0].split("-")[0]
        self.assertEqual(lines, [f"{winner_marker}-line-1", f"{winner_marker}-line-2"])
        self.assertEqual(set(results), {"alpha", "bravo"})


class FileLockTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lock = Path(self.tmp.name) / "x.lock"

    def test_threads_observe_mutual_exclusion(self) -> None:
        """Second thread must wait for first to release before it can enter."""
        timeline: list[tuple[float, str]] = []
        ready = threading.Event()
        first_in = threading.Event()

        def first():
            with file_lock(self.lock):
                first_in.set()
                timeline.append((time.monotonic(), "first-enter"))
                # Hold the lock long enough that thread #2 will queue
                time.sleep(0.05)
                timeline.append((time.monotonic(), "first-exit"))

        def second():
            ready.wait()
            with file_lock(self.lock):
                timeline.append((time.monotonic(), "second-enter"))

        t1 = threading.Thread(target=first)
        t2 = threading.Thread(target=second)
        t1.start()
        # Wait until first has the lock before starting second so the order is deterministic
        first_in.wait(timeout=1.0)
        ready.set()
        t2.start()
        t1.join(); t2.join()

        events = [name for _, name in timeline]
        self.assertEqual(events, ["first-enter", "first-exit", "second-enter"])

    def test_lock_path_for_is_deterministic(self) -> None:
        target = Path("/tmp/something/state.json")
        self.assertEqual(lock_path_for(target).name, "state.json.lock")
        self.assertEqual(lock_path_for(target), lock_path_for(target))


class EnsurePathWithinDirTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name) / "base"
        (self.base / "child").mkdir(parents=True)

    def test_accepts_path_inside_base(self) -> None:
        ensure_path_within_dir(self.base / "child" / "x", self.base, "Target")

    def test_rejects_genuine_escape(self) -> None:
        outside = Path(self.tmp.name) / "elsewhere"
        outside.mkdir()
        with self.assertRaises(ToolkitError):
            ensure_path_within_dir(outside, self.base, "Target")

    def test_case_variant_accepted_on_insensitive_fs(self) -> None:
        """On Windows/macOS-default the path normcase makes case-variant equal."""
        if os.path.normcase("ABC") == "ABC":
            # Linux / case-sensitive fs — uppercase != lowercase, skip this case.
            self.skipTest("case-sensitive filesystem, normcase is identity")
        # When normcase is a real lowercaser, a pure case difference must not be
        # treated as an escape.
        upcased = Path(str(self.base).upper()) / "child"
        ensure_path_within_dir(upcased, self.base, "Target")


if __name__ == "__main__":
    unittest.main()
