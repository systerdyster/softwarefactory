import subprocess
import sys
import unittest
from pathlib import Path

from support import SandboxTest

from fx import queue
from fx.errors import FactoryError

BIN = Path(__file__).resolve().parents[1]


class QueueTest(SandboxTest):
    def test_add_requires_the_approved_spec_and_plan(self):
        with self.assertRaises(FactoryError):
            queue.add(self.paths, "OPT-1", "No spec yet")

    def test_take_marks_the_oldest_queued_job_and_skips_taken_ones(self):
        self.sb.queue_job("001-first", "First")
        self.sb.queue_job("002-second", "Second")
        self.assertEqual(queue.take(self.paths), "001-first")
        self.assertEqual(queue.take(self.paths), "002-second")
        self.assertIsNone(queue.take(self.paths))
        self.assertIn("- [~] 001-first — First", self.paths.backlog.read_text())

    def test_duplicate_add_is_refused(self):
        self.sb.queue_job()
        with self.assertRaises(FactoryError):
            queue.add(self.paths, "001-answer", "Again")

    def test_mark_cancelled_and_done(self):
        job = self.sb.queue_job()
        queue.mark(self.paths, job, "cancelled")
        self.assertEqual(queue.entry(self.paths, job)["status"], "cancelled")

    def test_next_id_reserves_the_folder_so_two_sessions_never_share_an_id(self):
        first = queue.next_id(self.paths, "Fix the Login page!")
        second = queue.next_id(self.paths, "fix the login page")
        self.assertEqual(first, "001-fix-the-login-page")
        self.assertEqual(second, "002-fix-the-login-page")

    def test_concurrent_adds_from_separate_processes_all_land(self):
        script = (
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "from fx import queue; from fx.paths import Paths; from pathlib import Path;"
            "p = Paths(sys.argv[2]); j = sys.argv[3]; d = p.job(j); d.mkdir(parents=True);"
            "(d/'spec.md').write_text('s'); (d/'plan.md').write_text('p');"
            "queue.add(p, j, 'Job ' + j)"
        )
        procs = [subprocess.Popen([sys.executable, "-c", script, str(BIN), str(self.paths.root), f"{i:03d}-job"])
                 for i in range(1, 9)]
        self.assertEqual([p.wait() for p in procs], [0] * 8)
        self.assertEqual(sorted(e["id"] for e in queue.entries(self.paths)), [f"{i:03d}-job" for i in range(1, 9)])


if __name__ == "__main__":
    unittest.main()
