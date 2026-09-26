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


def _make_root():
    """Return a withdrawn Tk root, or None when no display is available."""
    if gui.tk is None:
        return None
    try:
        root = gui.tk.Tk()
    except gui.tk.TclError:
        return None
    root.withdraw()
    return root


class LogPipelineTests(unittest.TestCase):
    """The log queue must never be drained straight into the widget."""

    def setUp(self):
        self.root = _make_root()
        if self.root is None:
            self.skipTest("no Tk display available")
        self.app = gui.VideoFetcherGUI(self.root)

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def test_log_lines_are_queued_then_flushed_in_batches(self):
        self.app._log("queued-line\n", "info")
        # Nothing is inserted on the logging thread's behalf.
        self.assertEqual(self.app.log.get("1.0", "end").strip(), "")
        self.app._flush_log()
        self.assertIn("queued-line", self.app.log.get("1.0", "end"))

    def test_clear_sentinel_preserves_ordering(self):
        self.app._log("before\n", "info")
        self.app._flush_log()
        self.app._clear_log()
        self.app._log("after\n", "info")
        self.app._flush_log()
        text = self.app.log.get("1.0", "end")
        self.assertIn("after", text)
        self.assertNotIn("before", text)

    def test_widget_line_count_is_capped(self):
        for i in range(gui._LOG_MAX_LINES + 200):
            self.app._log(f"line-{i}\n", "dim")
        self.app._flush_log()
        lines = int(self.app.log.index("end-1c").split(".")[0])
        self.assertLessEqual(lines, gui._LOG_MAX_LINES + 1)


class CookieChainWiringTests(unittest.TestCase):
    """The GUI must delegate the cookie chain to fetch, not reimplement it."""

    def setUp(self):
        self.root = _make_root()
        if self.root is None:
            self.skipTest("no Tk display available")
        self.app = gui.VideoFetcherGUI(self.root)

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def test_chain_receives_emit_run_and_cancel_callbacks(self):
        seen = {}

        def fake_chain(url, out, high, config, bk, platform="generic", extra_args=None,
                       emit=None, run=None, is_cancelled=None):
            seen.update(emit=emit, run=run, is_cancelled=is_cancelled)
            emit("progress\n")
            return True, ""

        with patch.object(gui, "try_browser_cookies", fake_chain):
            ok = self.app._try_cookie_chain("https://x.com/a/status/1", "out", {}, "chrome", "twitter")

        self.assertTrue(ok)
        # Bound methods are re-created per attribute access, so compare the
        # underlying function and instance instead of object identity.
        self.assertIs(seen["emit"].__func__, type(self.app)._log)
        self.assertIs(seen["emit"].__self__, self.app)
        self.assertIs(seen["run"].__func__, type(self.app)._run_with_args)
        self.assertIs(seen["run"].__self__, self.app)
        self.assertIs(seen["is_cancelled"].__func__, self.app._cancel_event.is_set.__func__)
        self.assertIs(seen["is_cancelled"].__self__, self.app._cancel_event)

    def test_status_message_is_not_mistaken_for_ytdlp_output(self):
        def fake_chain(url, out, high, config, bk, platform="generic", extra_args=None,
                       emit=None, run=None, is_cancelled=None):
            return False, "chrome: native OK but douyin not logged in (missing: sessionid). Log in."

        with patch.object(gui, "try_browser_cookies", fake_chain):
            self.app._try_cookie_chain("https://www.douyin.com/video/1", "out", {}, "chrome", "douyin")

        self.assertEqual(self.app._last_ytdlp_tail, "")

    def test_real_error_text_is_kept_for_douyin_detection(self):
        message = "WARNING: Failed to download web detail JSON: HTTP Error 403: Forbidden"

        def fake_chain(url, out, high, config, bk, platform="generic", extra_args=None,
                       emit=None, run=None, is_cancelled=None):
            return False, message

        with patch.object(gui, "try_browser_cookies", fake_chain):
            self.app._try_cookie_chain("https://www.douyin.com/video/1", "out", {}, "chrome", "douyin")

        self.assertTrue(gui.is_douyin_web_detail_failure(self.app._last_ytdlp_tail))


if __name__ == "__main__":
    unittest.main()
