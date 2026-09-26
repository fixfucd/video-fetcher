import os
import subprocess
import unittest
from unittest.mock import Mock, patch

import gui


class ProcessGroupTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows-specific process flag")
    def test_windows_downloads_get_a_new_process_group(self):
        self.assertEqual(
            gui._popen_group_kwargs(),
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP},
        )

    @unittest.skipUnless(os.name == "nt", "Windows-specific process termination")
    def test_windows_termination_targets_the_owned_pid_tree(self):
        proc = Mock(pid=4321)
        proc.poll.return_value = None
        completed = Mock(returncode=0)
        with patch.object(gui.subprocess, "run", return_value=completed) as run:
            gui._terminate_process_tree(proc)
        run.assert_called_once_with(
            ["taskkill", "/PID", "4321", "/T", "/F"],
            capture_output=True,
            timeout=10,
        )
        proc.terminate.assert_not_called()

    @unittest.skipIf(os.name == "nt", "POSIX-specific process session")
    def test_posix_downloads_get_a_new_session(self):
        self.assertEqual(gui._popen_group_kwargs(), {"start_new_session": True})


if __name__ == "__main__":
    unittest.main()
