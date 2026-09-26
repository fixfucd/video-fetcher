import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _cookie_crypto


class CookieValueDecodeTests(unittest.TestCase):
    def test_strips_chromium_schema_v24_host_digest(self):
        host = ".douyin.com"
        plaintext = hashlib.sha256(host.encode("utf-8")).digest() + b"cookie-value"
        self.assertEqual(
            _cookie_crypto._decode_cookie_value(
                host, plaintext, require_host_digest=True
            ),
            "cookie-value",
        )

    def test_preserves_legacy_plaintext_without_digest(self):
        self.assertEqual(
            _cookie_crypto._decode_cookie_value("example.com", b"legacy-value"),
            "legacy-value",
        )

    def test_rejects_missing_or_mismatched_required_host_digest(self):
        self.assertIsNone(
            _cookie_crypto._decode_cookie_value(
                "example.com", b"legacy-value", require_host_digest=True
            )
        )
        wrong = hashlib.sha256(b"other.example").digest() + b"cookie-value"
        self.assertIsNone(
            _cookie_crypto._decode_cookie_value(
                "example.com", wrong, require_host_digest=True
            )
        )

    def test_legacy_plaintext_is_not_digest_stripped_when_the_schema_is_known(self):
        host = "example.com"
        digest = hashlib.sha256(host.encode("utf-8")).digest()
        self.assertIsNone(
            _cookie_crypto._decode_cookie_value(host, digest + b"value", require_host_digest=False)
        )
        # With an unknown schema the digest is recognised and stripped instead.
        self.assertEqual(
            _cookie_crypto._decode_cookie_value(host, digest + b"value", require_host_digest=None),
            "value",
        )

    def test_rejects_invalid_or_http_header_unsafe_text(self):
        self.assertIsNone(
            _cookie_crypto._decode_cookie_value("example.com", b"\xff\xfe")
        )
        self.assertIsNone(
            _cookie_crypto._decode_cookie_value(
                "example.com", "emoji-🙂".encode("utf-8")
            )
        )
        for value in (b"tab\tvalue", b"line\rvalue", b"line\nvalue", b"nul\0value"):
            with self.subTest(value=value):
                self.assertIsNone(
                    _cookie_crypto._decode_cookie_value("example.com", value)
                )

    def test_export_omits_invalid_values_and_keeps_netscape_shape(self):
        host = ".example.com"
        digest = hashlib.sha256(host.encode("utf-8")).digest()
        rows = [
            (host, "good", b"v10cipher1", "/", 0, 1),
            (host, "bad", b"v10cipher2", "/", 0, 1),
        ]

        def decrypt(_key, ciphertext):
            if ciphertext.endswith(b"1"):
                return digest + b"valid"
            return digest + b"invalid\tvalue"

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "cookies.txt"
            with patch.object(_cookie_crypto, "_init_aes", return_value="test"), patch.object(
                _cookie_crypto, "_find_db", return_value=("db", "state")
            ), patch.object(_cookie_crypto, "_get_key", return_value=b"key"), patch.object(
                _cookie_crypto, "_read_db", return_value=(rows, 24)
            ), patch.object(_cookie_crypto, "_aes_gcm_decrypt", side_effect=decrypt):
                self.assertTrue(_cookie_crypto.export_cookies("chrome", output))

            records = [
                line for line in output.read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#")
            ]
            self.assertEqual(len(records), 1)
            self.assertEqual(len(records[0].split("\t")), 7)
            self.assertIn("\tgood\tvalid", records[0])


class ExportAtomicityTests(unittest.TestCase):
    """A failed export must never truncate a cookies file that still works."""

    EXISTING = "# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tTRUE\t0\tSID\tkeep-me\n"

    def _run_export(self, tmp, rows, decrypt, version=10):
        output = Path(tmp) / "cookies.txt"
        output.write_text(self.EXISTING, encoding="utf-8")
        with patch.object(_cookie_crypto, "_init_aes", return_value="test"), patch.object(
            _cookie_crypto, "_find_db", return_value=("db", "state")
        ), patch.object(_cookie_crypto, "_get_key", return_value=b"key"), patch.object(
            _cookie_crypto, "_read_db", return_value=(rows, version)
        ), patch.object(_cookie_crypto, "_aes_gcm_decrypt", side_effect=decrypt), patch.object(
            _cookie_crypto, "_try_cdp_fallback", return_value=False
        ):
            ok = _cookie_crypto.export_cookies("chrome", output)
        return ok, output

    def test_failed_decryption_leaves_the_previous_file_intact(self):
        rows = [(".example.com", "SID", b"v10broken", "/", 0, 1)]
        with tempfile.TemporaryDirectory() as tmp:
            ok, output = self._run_export(tmp, rows, lambda _k, _c: None)
            self.assertFalse(ok)
            self.assertEqual(output.read_text(encoding="utf-8"), self.EXISTING)

    def test_undecryptable_only_rows_still_leave_the_previous_file_intact(self):
        # A v20-only database is the common case on current Chrome/Edge.
        rows = [(".example.com", "SID", b"v20appbound", "/", 0, 1)]
        with tempfile.TemporaryDirectory() as tmp:
            ok, output = self._run_export(tmp, rows, lambda _k, _c: b"unused")
            self.assertFalse(ok)
            self.assertEqual(output.read_text(encoding="utf-8"), self.EXISTING)

    def test_successful_export_replaces_the_previous_file(self):
        host = ".example.com"
        plaintext = hashlib.sha256(host.encode("utf-8")).digest() + b"fresh-value"
        rows = [(host, "SID", b"v10ok", "/", 0, 1)]
        with tempfile.TemporaryDirectory() as tmp:
            ok, output = self._run_export(tmp, rows, lambda _k, _c: plaintext, version=24)
            self.assertTrue(ok)
            text = output.read_text(encoding="utf-8")
            self.assertIn("fresh-value", text)
            self.assertNotIn("keep-me", text)

    def test_unknown_schema_version_still_yields_the_value(self):
        # meta.version unreadable -> None. Assuming "old schema" here used to
        # make every digest-prefixed value undecodable and drop it silently.
        host = ".example.com"
        plaintext = hashlib.sha256(host.encode("utf-8")).digest() + b"fresh-value"
        rows = [(host, "SID", b"v10ok", "/", 0, 1)]
        with tempfile.TemporaryDirectory() as tmp:
            ok, output = self._run_export(tmp, rows, lambda _k, _c: plaintext, version=None)
            self.assertTrue(ok)
            self.assertIn("fresh-value", output.read_text(encoding="utf-8"))

    def test_no_temporary_files_are_left_behind(self):
        rows = [(".example.com", "SID", b"v10broken", "/", 0, 1)]
        with tempfile.TemporaryDirectory() as tmp:
            self._run_export(tmp, rows, lambda _k, _c: None)
            leftover = [p.name for p in Path(tmp).iterdir() if p.name != "cookies.txt"]
            self.assertEqual(leftover, [])


class UnknownSchemaDecodeTests(unittest.TestCase):
    def test_unknown_schema_accepts_both_digest_and_legacy_values(self):
        host = ".example.com"
        digest = hashlib.sha256(host.encode("utf-8")).digest()
        self.assertEqual(
            _cookie_crypto._decode_cookie_value(host, digest + b"modern", require_host_digest=None),
            "modern",
        )
        self.assertEqual(
            _cookie_crypto._decode_cookie_value(host, b"legacy", require_host_digest=None),
            "legacy",
        )

    def test_known_schemas_keep_their_strict_rules(self):
        host = ".example.com"
        digest = hashlib.sha256(host.encode("utf-8")).digest()
        # v24 without the digest is rejected, and an old schema keeps the bytes.
        self.assertIsNone(
            _cookie_crypto._decode_cookie_value(host, b"legacy", require_host_digest=True)
        )
        self.assertIsNone(
            _cookie_crypto._decode_cookie_value(host, digest + b"modern", require_host_digest=False)
        )


if __name__ == "__main__":
    unittest.main()
