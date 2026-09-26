import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import fetch


class PlatformDetectionTests(unittest.TestCase):
    def test_detects_supported_hosts_and_short_links(self):
        cases = {
            "https://www.youtube.com/watch?v=1": "youtube",
            "youtu.be/abc": "youtube",
            "https://b23.tv/abc": "bilibili",
            "https://v.douyin.com/abc": "douyin",
            "https://mobile.x.com/user/status/1": "twitter",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(fetch.detect_platform(url), expected)

    def test_does_not_match_domain_substrings(self):
        self.assertEqual(fetch.detect_platform("https://notyoutube.com/watch?v=1"), "generic")
        self.assertEqual(fetch.detect_platform("https://example.com/?next=x.com"), "generic")
        self.assertEqual(fetch.detect_platform("http://[broken-host"), "generic")

    def test_normalizes_only_valid_douyin_modal_id(self):
        url = "https://www.douyin.com/jingxuan?foo=1&modal_id=7649969359930982011"
        self.assertEqual(
            fetch.normalize_douyin_url(url),
            "https://www.douyin.com/video/7649969359930982011",
        )
        foreign = "https://example.com/?modal_id=7649969359930982011"
        self.assertEqual(fetch.normalize_douyin_url(foreign), foreign)


class CookieTests(unittest.TestCase):
    def test_recognizes_douyin_web_detail_failure(self):
        output = (
            "Downloading web detail JSON\n"
            "HTTP Error 403: Forbidden\n"
            "Fresh cookies (not necessarily logged in) are needed"
        )
        self.assertTrue(fetch.is_douyin_web_detail_failure(output))
        self.assertFalse(fetch.is_douyin_web_detail_failure("HTTP Error 403: Forbidden"))

    def test_login_cookie_requires_real_domain_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            cookie_file = Path(tmp) / "cookies.txt"
            cookie_file.write_text(
                "# Netscape HTTP Cookie File\n"
                ".notyoutube.com\tTRUE\t/\tTRUE\t0\tSID\tsecret\n",
                encoding="utf-8",
            )
            logged, _, _ = fetch.check_cookie_login(cookie_file, "youtube")
            self.assertFalse(logged)

            cookie_file.write_text(
                "# Netscape HTTP Cookie File\n"
                ".accounts.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tsecret\n",
                encoding="utf-8",
            )
            logged, _, _ = fetch.check_cookie_login(cookie_file, "youtube")
            self.assertTrue(logged)

    def test_http_only_and_x_dot_com_login_cookies_are_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            cookie_file = Path(tmp) / "cookies.txt"
            cookie_file.write_text(
                "# Netscape HTTP Cookie File\n"
                "#HttpOnly_.accounts.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tsecret\n",
                encoding="utf-8",
            )
            self.assertTrue(fetch.check_cookie_login(cookie_file, "youtube")[0])

            cookie_file.write_text(
                "# Netscape HTTP Cookie File\n"
                "#HttpOnly_.x.com\tTRUE\t/\tTRUE\t0\tauth_token\tsecret\n",
                encoding="utf-8",
            )
            self.assertTrue(fetch.check_cookie_login(cookie_file, "twitter")[0])

    def test_temp_cookie_paths_are_unique_and_removable(self):
        first = fetch._make_temp_cookie_file("chrome", "native")
        second = fetch._make_temp_cookie_file("chrome", "native")
        try:
            self.assertNotEqual(first, second)
            self.assertTrue(Path(first).exists())
            self.assertTrue(Path(second).exists())
        finally:
            fetch._remove_temp_cookie_file(first)
            fetch._remove_temp_cookie_file(second)
        self.assertFalse(Path(first).exists())
        self.assertFalse(Path(second).exists())

    def test_douyin_anonymous_verification_cookie_is_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            cookie_file = Path(tmp) / "cookies.txt"
            cookie_file.write_text(
                "# Netscape HTTP Cookie File\n"
                ".douyin.com\tTRUE\t/\tTRUE\t0\ts_v_web_id\tverify-token\n",
                encoding="utf-8",
            )
            logged, missing, _ = fetch.check_cookie_login(cookie_file, "douyin")

        self.assertTrue(logged)
        self.assertEqual(missing, [])

    def test_douyin_anonymous_browser_cookies_reach_ytdlp(self):
        def export_anonymous(_browser_key, output_path):
            Path(output_path).write_text(
                "# Netscape HTTP Cookie File\n"
                ".douyin.com\tTRUE\t/\tTRUE\t0\ts_v_web_id\tverify-token\n"
                + "# padding\n" * 10,
                encoding="utf-8",
            )
            return True

        with tempfile.TemporaryDirectory() as out, patch.object(
            fetch, "_native_export", side_effect=export_anonymous
        ), patch.object(fetch, "_try_run", return_value=(0, "")) as run:
            ok, _ = fetch._try_browser(
                "https://www.douyin.com/video/7674279232511429934",
                out,
                {},
                {},
                "chrome",
                platform="douyin",
            )

        self.assertTrue(ok)
        run.assert_called_once()


class BrowserDetectionTests(unittest.TestCase):
    def test_installed_browser_does_not_require_an_existing_cookie_database(self):
        def executable_exists(key):
            return key == "chrome"

        with patch.object(fetch, "detect_browser_profiles", return_value=[]), patch.object(
            fetch, "_browser_executable_exists", side_effect=executable_exists
        ), patch.object(fetch, "find_cookies_file", return_value=(None, None)):
            detected = fetch.detect_installed_browsers()

        self.assertTrue(detected["chrome"]["installed"])
        self.assertEqual(detected["chrome"]["profiles"], 0)
        self.assertFalse(detected["edge"]["installed"])


class ToolDetectionTests(unittest.TestCase):
    def test_ffmpeg_uses_its_supported_version_flag(self):
        completed = Mock(returncode=0)
        with patch.object(fetch.subprocess, "run", return_value=completed) as run:
            self.assertTrue(fetch.check_tool("ffmpeg"))
        run.assert_called_once_with(["ffmpeg", "-version"], capture_output=True, timeout=10)

    def test_child_python_tools_are_forced_to_utf8(self):
        env = fetch._utf8_subprocess_env()
        self.assertEqual(env["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(env["PYTHONUTF8"], "1")


class FetchStrategyTests(unittest.TestCase):
    @staticmethod
    def _base_patches(run_result):
        return (
            patch.object(fetch, "load_config", return_value={}),
            patch.object(fetch, "detect_installed_browsers", return_value={}),
            patch.object(fetch, "get_available_browsers", return_value=[]),
            patch.object(fetch, "_has_bc3", return_value=False),
            patch.object(fetch, "_has_cdp", return_value=False),
            patch.object(fetch, "_try_run", side_effect=run_result),
            patch.object(fetch, "set_log_file"),
        )

    def test_public_twitter_succeeds_without_cookie_chain(self):
        patches = self._base_patches([(0, "")])
        with tempfile.TemporaryDirectory() as out:
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as run, patches[6]:
                rc = fetch.fetch("https://x.com/user/status/1", output_dir=out)
        self.assertEqual(rc, 0)
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("--cookies", run.call_args.args[0])
        self.assertNotIn("--cookies-from-browser", run.call_args.args[0])

    def test_failed_public_twitter_attempt_is_not_repeated(self):
        patches = self._base_patches([(7, "protected")])
        with tempfile.TemporaryDirectory() as out:
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as run, patches[6]:
                rc = fetch.fetch("https://x.com/user/status/1", output_dir=out)
        self.assertEqual(rc, 7)
        self.assertEqual(run.call_count, 1)

    def test_youtube_presets_do_not_force_obsolete_player_clients(self):
        high, fallback = fetch.get_platform_presets("youtube", {})
        self.assertNotIn("extractor_args", high)
        self.assertNotIn("extractor_args", fallback)

    def test_douyin_has_no_redundant_low_quality_tier(self):
        # A fallback whose format matches the HD preset would repeat the request
        # that just failed, so douyin declares no separate tier at all.
        high, fallback = fetch.get_platform_presets("douyin", {})
        self.assertIsNone(fallback)
        self.assertEqual(high["format"], "bestvideo+bestaudio/best")

    def test_same_tier_detects_only_identical_downloads(self):
        self.assertTrue(fetch.same_tier({"format": "best"}, {"format": "best"}))
        self.assertFalse(fetch.same_tier(
            {"format": "bestvideo+bestaudio/best"},
            {"format": "bestvideo[height<=720]+bestaudio/best"},
        ))
        self.assertFalse(fetch.same_tier(
            {"format": "best", "extractor_args": "youtube:player_client=web"},
            {"format": "best", "extractor_args": "youtube:player_client=android"},
        ))
        self.assertFalse(fetch.same_tier(None, {"format": "best"}))

    def test_douyin_failure_skips_the_no_cookie_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            cookie_file = Path(tmp) / "cookies.txt"
            cookie_file.write_text(
                "# Netscape HTTP Cookie File\n"
                ".douyin.com\tTRUE\t/\tTRUE\t0\tsessionid\tsecret\n",
                encoding="utf-8",
            )
            config = {"cookies_file": str(cookie_file)}
            with patch.object(fetch, "load_config", return_value=config), patch.object(
                fetch, "detect_installed_browsers", return_value={}
            ), patch.object(fetch, "_has_bc3", return_value=False), patch.object(
                fetch, "_has_cdp", return_value=False
            ), patch.object(
                fetch, "_try_run", return_value=(1, "HTTP Error 403: Forbidden")
            ) as run, patch.object(fetch, "set_log_file"):
                fetch.fetch("https://www.douyin.com/video/1", output_dir=tmp)

        # Exactly one request: the cookie-file attempt. No fake "LQ" retry.
        self.assertEqual(run.call_count, 1)

    def test_alternates_reuse_the_detection_pass(self):
        installed = {
            "chrome": {"installed": True, "profiles": 1, "label": "Chrome", "key": "chrome"},
            "edge": {"installed": True, "profiles": 1, "label": "Edge", "key": "edge"},
        }
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            fetch, "load_config", return_value={}
        ), patch.object(
            fetch, "detect_installed_browsers", return_value=installed
        ) as detect, patch.object(
            fetch, "get_available_browsers", side_effect=AssertionError("must not rescan")
        ), patch.object(
            fetch, "_has_bc3", return_value=False
        ), patch.object(
            fetch, "_has_cdp", return_value=False
        ), patch.object(
            fetch, "_try_run", return_value=(1, "boom")
        ), patch.object(
            fetch, "_try_browser", return_value=(False, "boom")
        ) as browser, patch.object(fetch, "set_log_file"):
            fetch.fetch("https://example.com/v", output_dir=tmp)

        self.assertEqual(detect.call_count, 1)
        self.assertEqual([c.args[4] for c in browser.call_args_list], ["chrome", "edge"])

    def test_not_logged_in_keeps_a_reason_instead_of_an_empty_tail(self):
        def export_without_login(_browser_key, output_path):
            Path(output_path).write_text(
                "# Netscape HTTP Cookie File\n"
                ".other.com\tTRUE\t/\tTRUE\t0\tSID\tsecret\n" + "# padding\n" * 10,
                encoding="utf-8",
            )
            return True

        with tempfile.TemporaryDirectory() as out, patch.object(
            fetch, "_native_export", side_effect=export_without_login
        ), patch.object(fetch, "_try_run", return_value=(0, "")) as run:
            ok, reason = fetch._try_browser(
                "https://www.youtube.com/watch?v=1", out, {}, {}, "chrome", platform="youtube"
            )

        self.assertFalse(ok)
        self.assertIn("not logged in", reason)
        run.assert_not_called()

    def test_douyin_web_detail_failure_skips_only_no_cookie_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            cookie_file = Path(tmp) / "cookies.txt"
            cookie_file.write_text(
                "# Netscape HTTP Cookie File\n"
                ".douyin.com\tTRUE\t/\tTRUE\t0\tsessionid\tsecret\n",
                encoding="utf-8",
            )
            config = {"cookies_file": str(cookie_file)}
            error = "Downloading web detail JSON\nHTTP Error 403: Forbidden"
            with patch.object(fetch, "load_config", return_value=config), patch.object(
                fetch, "detect_installed_browsers", return_value={}
            ), patch.object(fetch, "get_available_browsers", return_value=[]), patch.object(
                fetch, "_has_bc3", return_value=False
            ), patch.object(fetch, "_has_cdp", return_value=False), patch.object(
                fetch, "_try_run", return_value=(1, error)
            ) as run, patch.object(fetch, "set_log_file"):
                rc = fetch.fetch(
                    "https://www.douyin.com/video/7674279232511429934",
                    output_dir=tmp,
                )

        self.assertEqual(rc, 1)
        self.assertEqual(run.call_count, 1)

    def test_douyin_web_detail_failure_still_allows_another_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            cookie_file = Path(tmp) / "cookies.txt"
            cookie_file.write_text(
                "# Netscape HTTP Cookie File\n"
                ".douyin.com\tTRUE\t/\tTRUE\t0\tsessionid\tsecret\n",
                encoding="utf-8",
            )
            config = {"cookies_file": str(cookie_file)}
            error = "Downloading web detail JSON\nHTTP Error 403: Forbidden"
            installed = {"chrome": {"installed": True}}
            with patch.object(fetch, "load_config", return_value=config), patch.object(
                fetch, "detect_installed_browsers", return_value=installed
            ), patch.object(fetch, "get_available_browsers", return_value=["chrome"]), patch.object(
                fetch, "_has_bc3", return_value=False
            ), patch.object(fetch, "_has_cdp", return_value=False), patch.object(
                fetch, "_try_run", return_value=(1, error)
            ) as run, patch.object(
                fetch, "_try_browser", return_value=(True, "")
            ) as browser, patch.object(fetch, "set_log_file"):
                rc = fetch.fetch(
                    "https://www.douyin.com/video/7674279232511429934",
                    output_dir=tmp,
                )

        self.assertEqual(rc, 0)
        self.assertEqual(run.call_count, 1)
        browser.assert_called_once()


if __name__ == "__main__":
    unittest.main()
