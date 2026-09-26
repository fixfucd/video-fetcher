import os
import shutil
import subprocess
import tempfile
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


class _ProjectDirOs:
    """os shim that resolves project-relative paths inside a scratch dir."""

    def __init__(self, base):
        self._base = base

    def __getattr__(self, name):
        return getattr(os, name)

    @property
    def path(self):
        return _ProjectDirOsPath(self._base)


class _ProjectDirOsPath:
    def __init__(self, base):
        self._base = base

    def __getattr__(self, name):
        return getattr(os.path, name)

    def abspath(self, p):
        return os.path.join(self._base, os.path.basename(os.path.abspath(p)))

    def dirname(self, p):
        # os.path.join is not redirected, so anything joined onto a "cookies"
        # dirname must land inside the scratch dir as well. mkdtemp inherits this
        # and creates the export scratch dirs under the same root.
        return self._base

    def isfile(self, p):
        # The export path probes for a real cookies database; this test only
        # exercises what happens after that probe succeeds.
        return True

    def getsize(self, p):
        # Only used by the "is a cookies database present" probe, which this
        # test always answers yes to. Production code never asks this shim for
        # the size of an exported file (that goes through the real os).
        return 1024


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


class CookieExportSafetyTests(unittest.TestCase):
    """Export & Use must never destroy a cookies file that still works."""

    SAVED_NAME = "chrome.txt"

    def setUp(self):
        self.root = _make_root()
        if self.root is None:
            self.skipTest("no Tk display available")
        self.app = gui.VideoFetcherGUI(self.root)
        self.app.cookies_browser_var.set("chrome")
        self.dir = tempfile.mkdtemp(prefix="vf_gui_export_test_")
        os.makedirs(os.path.join(self.dir, "cookies"), exist_ok=True)
        # A real file for the "is the browser installed" probe to find.
        self.profile_dir = os.path.join(self.dir, "profile", "Network")
        os.makedirs(self.profile_dir, exist_ok=True)
        with open(os.path.join(self.profile_dir, "Cookies"), "w", encoding="utf-8") as f:
            f.write("placeholder")
        self.saved = os.path.join(self.dir, "cookies", self.SAVED_NAME)
        self.existing = ("# Netscape HTTP Cookie File\n"
                         ".example.com\tTRUE\t/\tTRUE\t0\tSID\tkeep-me\n")
        with open(self.saved, "w", encoding="utf-8") as f:
            f.write(self.existing)
        # Redirect only gui's view of the project directory. Replacing the shared
        # os.path.abspath would also redirect tempfile and break the real files.
        self._os_patch = patch.object(gui, "os", _ProjectDirOs(self.dir))
        self._os_patch.start()

    def tearDown(self):
        self._os_patch.stop()
        shutil.rmtree(self.dir, ignore_errors=True)
        try:
            self.root.destroy()
        except Exception:
            pass

    def _export_with(self, native_writer):
        with patch.object(gui, "_native_export", native_writer), \
                patch.object(gui, "bc3_export", return_value=False), \
                patch.object(gui, "messagebox"), \
                patch.object(gui, "_expand_path", side_effect=lambda p, *a: p), \
                patch.object(gui, "detect_browser_profiles",
                             return_value=[(os.path.join(self.dir, "profile"), "Default")]), \
                patch.object(self.app, "_show_cookie_result"), \
                patch.object(self.app, "_save_config"), \
                patch.object(self.app, "_update_cookies_status"):
            self.app._export_and_use()
            self.app._flush_log()

    def test_all_methods_failing_keeps_the_previous_export(self):
        def produce_nothing(_key, path):
            # No usable export: the method fails, and nothing may be published.
            return False

        self._export_with(produce_nothing)

        with open(self.saved, encoding="utf-8") as f:
            self.assertEqual(f.read(), self.existing)
        self.assertIn("Kept the existing export", self.app.log.get("1.0", "end"))

    def test_successful_export_replaces_the_previous_file(self):
        payload = ("# Netscape HTTP Cookie File\n# video-fetcher\n\n"
                   + ".example.com\tTRUE\t/\tTRUE\t0\tSID\tfresh\n" + "# pad\n" * 20)

        def write_full(_key, path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(payload)
            return True

        self._export_with(write_full)

        with open(self.saved, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("fresh", text)
        self.assertNotIn("keep-me", text)

    def test_no_partial_files_are_left_in_the_target_directory(self):
        def produce_nothing(_key, path):
            return False

        self._export_with(produce_nothing)

        leftovers = [n for n in os.listdir(os.path.join(self.dir, "cookies"))
                     if n != self.SAVED_NAME]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
