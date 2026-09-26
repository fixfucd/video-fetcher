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

    def test_legacy_plaintext_is_not_digest_stripped(self):
        host = "example.com"
        digest = hashlib.sha256(host.encode("utf-8")).digest()
        self.assertIsNone(_cookie_crypto._decode_cookie_value(host, digest + b"value"))

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


if __name__ == "__main__":
    unittest.main()
