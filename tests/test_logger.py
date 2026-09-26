import io
import unittest
from unittest.mock import patch

import _logger


class LoggerTests(unittest.TestCase):
    def test_log_works_without_console_streams(self):
        for level, message in (("info", "GUI started"), ("error", "GUI failed")):
            with self.subTest(level=level):
                output = io.StringIO()
                with patch.object(_logger.sys, "stdout", None), patch.object(
                    _logger.sys, "stderr", None
                ), patch.object(_logger, "_LOG_HANDLE", output):
                    _logger.log(level, message)

                self.assertIn(message, output.getvalue())


if __name__ == "__main__":
    unittest.main()
