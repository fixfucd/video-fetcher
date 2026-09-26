"""Frame-parsing and process-lifecycle tests for the CDP cookie fallback."""
import json
import os
import socket
import struct
import subprocess
import threading
import unittest
from unittest.mock import Mock, patch

import _cdp_cookies


# ──────────────────────────────────────────────────────────────
#  A scripted WebSocket peer
# ──────────────────────────────────────────────────────────────

def _mask(payload):
    key = b"\x01\x02\x03\x04"
    return key + bytes(b ^ key[i % 4] for i, b in enumerate(payload))


def _frame(payload, opcode=0x1):
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack(">H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack(">Q", length))
    return bytes(header) + _mask(payload)


class _FakePeer:
    """Accepts one connection and replays a scripted byte sequence."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self._thread = None

    def replay(self, chunks):
        def serve():
            try:
                conn, _ = self.sock.accept()
                for chunk in chunks:
                    conn.sendall(chunk)
                import time
                time.sleep(1.0)
                conn.close()
            except OSError:
                pass
        self._thread = threading.Thread(target=serve, daemon=True)
        self._thread.start()
        return self

    def connect(self):
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(5)
        client.connect(("127.0.0.1", self.port))
        return client

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class WebSocketFrameTests(unittest.TestCase):
    def _recv_from(self, chunks, timeout=3):
        peer = _FakePeer().replay(chunks)
        try:
            return _cdp_cookies._ws_recv(peer.connect(), timeout=timeout)
        finally:
            peer.close()

    def test_eof_inside_extended_length_header_raises_instead_of_spinning(self):
        # Second byte claims a 2-byte extended length, then the peer vanishes.
        # The old loop did `ext += sock.recv(...)`, which never grows on b''
        # and spun at 100% CPU forever.
        done = []

        def run():
            try:
                self._recv_from([b"\x81\xfe"])
            except ConnectionError:
                done.append("raised")
            except Exception as exc:  # pragma: no cover - diagnostic
                done.append(f"other:{type(exc).__name__}")
            else:
                done.append("returned")

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(timeout=10)
        self.assertFalse(worker.is_alive(), "_ws_recv hung on EOF inside the length header")
        self.assertEqual(done, ["raised"])

    def test_eof_inside_64bit_length_header_raises(self):
        with self.assertRaises(ConnectionError):
            self._recv_from([b"\x81\xff\x00\x00"])

    def test_oversized_frame_is_rejected_without_allocating(self):
        header = b"\x81\xff" + struct.pack(">Q", _cdp_cookies.MAX_WS_PAYLOAD + 1)
        with self.assertRaises(ConnectionError):
            self._recv_from([header])

    def test_tolerates_a_large_masked_frame(self):
        payload = json.dumps({"id": 1, "result": {"pad": "x" * 300}}).encode()
        opcode, raw = self._recv_from([_frame(payload)])
        self.assertEqual(opcode, 0x1)
        self.assertEqual(json.loads(raw.decode()), {"id": 1, "result": {"pad": "x" * 300}})

    def test_control_frames_are_skipped_not_parsed_as_json(self):
        text = json.dumps({"id": 1, "result": {"cookies": []}}).encode()
        opcode, raw = self._recv_from([_frame(b"ping", opcode=0x9), _frame(text)])
        self.assertEqual(opcode, 0x1)
        self.assertEqual(json.loads(raw.decode())["id"], 1)

    def test_close_frame_ends_the_read(self):
        with self.assertRaises(ConnectionError):
            self._recv_from([_frame(b"", opcode=0x8)])

    def test_binary_frame_is_rejected(self):
        with self.assertRaises(ConnectionError):
            self._recv_from([_frame(b"\x00\x01", opcode=0x2)])


class CdpCallTests(unittest.TestCase):
    def test_skips_event_frames_and_matches_the_request_id(self):
        sent = []
        event = json.dumps({"method": "Network.loadingFinished", "params": {}}).encode()
        reply = json.dumps({"id": 7, "result": {"cookies": [{"name": "SID"}]}}).encode()
        frames = [(0x1, event), (0x1, reply)]

        with patch.object(_cdp_cookies, "_ws_send", side_effect=lambda s, t: sent.append(t)), \
                patch.object(_cdp_cookies, "_ws_recv", side_effect=lambda s, t: frames.pop(0)):
            result = _cdp_cookies._cdp_call(Mock(), 7, "Storage.getCookies")

        self.assertEqual(result["result"]["cookies"][0]["name"], "SID")
        self.assertEqual(json.loads(sent[0])["id"], 7)

    def test_cdp_error_is_reported_instead_of_swallowed(self):
        reply = json.dumps({"id": 3, "error": {"code": -32601, "message": "no such method"}}).encode()

        with patch.object(_cdp_cookies, "_ws_send"), \
                patch.object(_cdp_cookies, "_ws_recv", return_value=(0x1, reply)):
            with self.assertRaises(RuntimeError):
                _cdp_cookies._cdp_call(Mock(), 3, "Storage.getCookies")

    def test_missing_reply_times_out(self):
        with patch.object(_cdp_cookies, "_ws_send"), \
                patch.object(_cdp_cookies, "_ws_recv", return_value=(0x1, b'{"method":"noise"}')):
            with self.assertRaises(TimeoutError):
                _cdp_cookies._cdp_call(Mock(), 1, "Storage.getCookies", timeout=0.3)


class BrowserLifecycleTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows-specific process-tree kill")
    def test_windows_kill_targets_the_whole_process_tree(self):
        proc = Mock(pid=7788)
        proc.poll.return_value = None
        completed = Mock(returncode=0)
        with patch.object(_cdp_cookies.subprocess, "run", return_value=completed) as run:
            _cdp_cookies._kill_browser(proc)
        run.assert_called_once_with(
            ["taskkill", "/PID", "7788", "/T", "/F"], capture_output=True, timeout=10
        )
        proc.terminate.assert_not_called()

    def test_dead_process_is_not_killed_again(self):
        proc = Mock(pid=1)
        proc.poll.return_value = 0
        with patch.object(_cdp_cookies.subprocess, "run") as run:
            _cdp_cookies._kill_browser(proc)
        run.assert_not_called()
        proc.terminate.assert_not_called()

    def test_graceful_close_gets_a_chance_before_the_tree_is_killed(self):
        proc = Mock(pid=4242)
        proc.poll.return_value = None
        with patch.object(_cdp_cookies.subprocess, "run", return_value=Mock(returncode=0)) as run:
            _cdp_cookies._kill_browser(proc, wait_first=5)
        proc.wait.assert_called_once_with(timeout=5)
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
