"""
_cdp_cookies.py — Zero-dep cookie extraction via Chrome DevTools Protocol
Used as fallback when file-based DPAPI decryption fails (Chrome/Edge v20
App-Bound Encryption).

Launches the browser with --remote-debugging-port, connects via raw WebSocket,
calls Storage.getCookies (browser decrypts cookies internally), then terminates.
"""
import os, json, time, socket, base64, struct, secrets, subprocess, tempfile

# Upper bound for a single WebSocket frame. Storage.getCookies for a real
# profile stays far below this; the cap stops a rogue local process (which could
# race us onto the debug port) from making us allocate unbounded memory.
MAX_WS_PAYLOAD = 64 * 1024 * 1024

# ──────────────────────────────────────────────────────────────
#  Minimal RFC 6455 WebSocket client (zero external deps)
# ──────────────────────────────────────────────────────────────

def _ws_connect(ws_url, timeout=10):
    """Connect to a ws:// URL, return socket after handshake."""
    u = ws_url.replace('ws://', '')
    if '/' in u:
        hp, path = u.split('/', 1)
    else:
        hp, path = u, ''
    path = '/' + path
    host, port_s = (hp.split(':') + ['80'])[:2]
    port = int(port_s)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect((host, port))

    key = base64.b64encode(secrets.token_bytes(16)).decode()
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(req.encode())

    response = b''
    while b'\r\n\r\n' not in response:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("WebSocket handshake: connection closed")
        response += chunk

    if b'101' not in response:
        raise ConnectionError(f"WebSocket handshake rejected: {response[:200]}")
    return sock


def _ws_send(sock, text):
    """Send a text frame (client→server, masked)."""
    payload = text.encode('utf-8')
    mask_key = secrets.token_bytes(4)

    header = bytearray([0x81])  # FIN + text opcode
    plen = len(payload)
    if plen < 126:
        header.append(0x80 | plen)
    elif plen < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack('>H', plen))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack('>Q', plen))
    header.extend(mask_key)

    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    sock.sendall(bytes(header) + masked)


def _recv_exact(sock, count):
    """Read exactly ``count`` bytes or raise (never returns a short read).

    A bare ``sock.recv(n)`` returns b'' on EOF; adding that to a buffer leaves
    the length unchanged, so the old loops spun at 100% CPU and never returned.
    """
    data = b''
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ConnectionError("WebSocket recv: connection closed mid-frame")
        data += chunk
    return data


def _ws_recv(sock, timeout=10):
    """Read one complete data frame (server→client), skipping control frames.

    Returns (opcode, payload). Raises ConnectionError on a close frame, a
    truncated frame, an oversized payload, or a protocol error.
    """
    sock.settimeout(timeout)

    while True:
        header = _recv_exact(sock, 2)
        fin = (header[0] & 0x80) != 0
        opcode = header[0] & 0x0F
        masked = (header[1] & 0x80) != 0
        payload_len = header[1] & 0x7F

        if payload_len == 126:
            payload_len = struct.unpack('>H', _recv_exact(sock, 2))[0]
        elif payload_len == 127:
            payload_len = struct.unpack('>Q', _recv_exact(sock, 8))[0]

        if payload_len > MAX_WS_PAYLOAD:
            raise ConnectionError(f"WebSocket frame too large: {payload_len} bytes")

        mask_key = _recv_exact(sock, 4) if masked else None
        payload = _recv_exact(sock, payload_len) if payload_len else b''
        if mask_key:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        if opcode == 0x8:
            raise ConnectionError("WebSocket closed by peer")
        if opcode in (0x9, 0xA):
            # Control frames may be interleaved with the response; a truncated
            # payload must never reach the JSON parser.
            continue
        if not fin:
            raise ConnectionError("WebSocket fragmented frame is not supported")
        if opcode not in (0x1, 0x2):
            raise ConnectionError(f"WebSocket unexpected opcode {opcode}")
        if opcode == 0x2:
            raise ConnectionError("WebSocket binary frame where text was expected")
        return opcode, payload


# ──────────────────────────────────────────────────────────────
#  CDP helpers
# ──────────────────────────────────────────────────────────────

def _cdp_call(sock, _id, method, params=None, timeout=10):
    """Send a CDP command and return the response matching its id.

    Frames that are not the answer (events, or a reply to an earlier id) are
    skipped instead of being parsed as the result.
    """
    msg = {"id": _id, "method": method}
    if params:
        msg["params"] = params
    _ws_send(sock, json.dumps(msg, ensure_ascii=False))

    deadline = time.time() + timeout
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            raise TimeoutError(f"CDP {method}: no reply within {timeout}s")
        _, raw = _ws_recv(sock, remaining)
        try:
            reply = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            continue  # not JSON we understand; keep waiting for the reply
        if isinstance(reply, dict) and reply.get("id") == _id:
            error = reply.get("error")
            if error:
                raise RuntimeError(f"CDP {method} failed: {error}")
            return reply


# ──────────────────────────────────────────────────────────────
#  Browser lifecycle
# ──────────────────────────────────────────────────────────────

def _pick_port(start=9222, end=9250):
    """Find a free TCP port for remote debugging."""
    for port in range(start, end):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(('127.0.0.1', port))
            s.close()
            return port
        except OSError:
            continue
    raise OSError(f"No free port in range {start}-{end}")


def _find_exe(candidates):
    """Return first existing file from a list of candidate paths."""
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def _find_chrome():
    return _find_exe([
        os.path.join(os.environ.get('ProgramFiles', 'C:\\Program Files'),
                     'Google', 'Chrome', 'Application', 'chrome.exe'),
        os.path.join(os.environ.get('ProgramFiles(x86)', 'C:\\Program Files (x86)'),
                     'Google', 'Chrome', 'Application', 'chrome.exe'),
    ])


def _find_edge():
    return _find_exe([
        os.path.join(os.environ.get('ProgramFiles(x86)', 'C:\\Program Files (x86)'),
                     'Microsoft', 'Edge', 'Application', 'msedge.exe'),
        os.path.join(os.environ.get('ProgramFiles', 'C:\\Program Files'),
                     'Microsoft', 'Edge', 'Application', 'msedge.exe'),
        os.path.join(os.environ.get('LOCALAPPDATA', ''),
                     'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    ])


def _find_brave():
    return _find_exe([
        os.path.join(os.environ.get('ProgramFiles', 'C:\\Program Files'),
                     'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe'),
        os.path.join(os.environ.get('ProgramFiles(x86)', 'C:\\Program Files (x86)'),
                     'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe'),
    ])


_BROWSER_FINDERS = {
    'chrome': _find_chrome,
    'edge': _find_edge,
    'brave': _find_brave,
}

# User data directories per browser (in priority order)
_BROWSER_USER_DATA = {
    'chrome':  [('{localappdata}/Google/Chrome/User Data',)],
    'edge':    [('{localappdata}/Microsoft/Edge/User Data',)],
    'brave':   [('{localappdata}/BraveSoftware/Brave-Browser/User Data',)],
}


def _find_user_data_dir(browser_key):
    """Find user data directory for a given browser."""
    local = os.environ.get('LOCALAPPDATA', '')
    for group in _BROWSER_USER_DATA.get(browser_key, []):
        base = group[0].replace('{localappdata}', local)
        if os.path.isdir(base):
            return base
    return None


def _kill_browser(proc, wait_first=0):
    """Terminate the browser process tree we started.

    Chromium runs as several processes. Terminating only the process we spawned
    leaves the GPU/utility/renderer children behind holding the profile lock,
    which makes every later CDP attempt fail. On Windows ask taskkill for the
    whole tree; elsewhere fall back to terminate/kill.

    ``wait_first`` gives a browser that already received Browser.close a few
    seconds to exit on its own before it is forced down.
    """
    if proc is None:
        return
    if wait_first:
        try:
            proc.wait(timeout=wait_first)
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass
    if proc.poll() is None:
        if os.name == 'nt':
            try:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, timeout=10)
                return
            except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
                pass
    else:
        return  # already exited; nothing left to terminate
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _request_browser_close(sock):
    """Ask the browser to shut itself down before we force the tree down."""
    try:
        _cdp_call(sock, 9999, 'Browser.close', timeout=5)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────
#  Main export
# ──────────────────────────────────────────────────────────────

def export_cookies_cdp(output_path, browser_key=None, browser_exe=None, user_data_dir=None, timeout=30):
    """
    Extract cookies from a Chromium browser via CDP, write Netscape format.
    
    Provide either browser_key (auto-detect exe + user_data_dir) or
    explicit browser_exe + user_data_dir. Returns True on success.

    browser_key defaults to 'chrome' so that exe and user_data_dir can never
    disagree; pass an explicit key to target another Chromium browser.
    """
    browser_key = browser_key or 'chrome'

    # Resolve browser executable
    if browser_exe is None:
        finder = _BROWSER_FINDERS.get(browser_key)
        if finder:
            browser_exe = finder()
    if not browser_exe or not os.path.isfile(browser_exe):
        return False

    # Resolve user data directory
    if user_data_dir is None:
        user_data_dir = _find_user_data_dir(browser_key)
    if not user_data_dir or not os.path.isdir(user_data_dir):
        return False

    port = _pick_port()
    proc = None
    closed_gracefully = False

    try:
        # Never terminate the user's existing browser. If the profile is in use,
        # the debug endpoint may not start and this method will fail cleanly; the
        # caller can ask the user to close the browser and retry.
        # Launch browser with remote debugging
        proc = subprocess.Popen(
            [browser_exe,
             f'--user-data-dir={user_data_dir}',
             f'--remote-debugging-port={port}',
             '--no-first-run',
             '--no-default-browser-check',
             '--disable-extensions',
             '--disable-background-networking',
             '--disable-sync',
             '--disable-features=TranslateUI',
             '--disable-gpu',
             '--window-size=1,1',
             '--window-position=-32000,-32000'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait for the debugger endpoint. Chrome may re-exec and hand the
        # request to an already running instance, after which OUR process exits
        # while the browser lives on — so treat "our process ended" as fatal only
        # while the endpoint has never answered.
        deadline = time.time() + timeout
        ws_url = None
        endpoint_seen = False
        while time.time() < deadline:
            if proc.poll() is not None and not endpoint_seen:
                return False

            try:
                import urllib.request
                resp = urllib.request.urlopen(
                    f'http://127.0.0.1:{port}/json/version', timeout=2
                )
                info = json.loads(resp.read())
                endpoint_seen = True
                ws_url = info.get('webSocketDebuggerUrl', '')
                if ws_url:
                    break
            except Exception:
                pass
            # Also paced on the "reachable but no ws url yet" path, which used to
            # spin on the endpoint as fast as the loop could go.
            time.sleep(0.5)

        if not ws_url:
            return False

        # Connect WebSocket and extract cookies
        # A single frame may not take longer than this, however long the overall
        # endpoint wait was allowed to be.
        frame_timeout = min(timeout, 20)
        sock = _ws_connect(ws_url, timeout=10)

        try:
            # CDP: Storage.getCookies returns all cookies in all contexts
            result = _cdp_call(sock, 1, 'Storage.getCookies', timeout=frame_timeout)
            cookies = result.get('result', {}).get('cookies', [])
            _request_browser_close(sock)
            closed_gracefully = True
        finally:
            try:
                sock.close()
            except Exception:
                pass

        if not cookies:
            return False

        # Write beside the target and swap it in only on success, so a failed
        # extraction cannot destroy an existing cookie file.
        fd, tmp_path = tempfile.mkstemp(
            prefix=os.path.basename(output_path) + ".cdp", suffix=".part",
            dir=os.path.dirname(os.path.abspath(output_path)) or None,
        )
        os.close(fd)
        try:
            if not _write_netscape(tmp_path, cookies):
                return False
            os.replace(tmp_path, output_path)
            return True
        except OSError:
            return False
        finally:
            try: os.unlink(tmp_path)
            except OSError: pass

    except Exception:
        return False
    finally:
        # A browser that accepted Browser.close gets a moment to exit cleanly;
        # anything still alive is forced down as a tree.
        _kill_browser(proc, wait_first=5 if closed_gracefully else 0)


def _write_netscape(output_path, cookies):
    """Write cookies in Netscape HTTP Cookie File format."""
    cnt = 0
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('# Netscape HTTP Cookie File\n# video-fetcher (CDP)\n\n')
            for c in cookies:
                domain = c.get('domain', '')
                flag = 'TRUE' if domain.startswith('.') else 'FALSE'
                path = c.get('path', '/')
                secure = 'TRUE' if c.get('secure', False) else 'FALSE'
                # CDP returns expiry as double (seconds since epoch)
                expires = str(int(c.get('expires', 0))) if c.get('expires') else '0'
                name = c.get('name', '')
                value = c.get('value', '')
                # Skip empty names/values
                if not name and not value:
                    continue
                f.write(f'{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n')
                cnt += 1
        return cnt > 0
    except OSError:
        return False


# ──────────────────────────────────────────────────────────────
#  Quick test
# ──────────────────────────────────────────────────────────────

if __name__ == '__main__':
    tmp = os.path.join(tempfile.gettempdir(), 'cdp_cookies_test.txt')
    ok = export_cookies_cdp(tmp)
    if ok:
        sz = os.path.getsize(tmp)
        print(f'OK: {tmp} ({sz} bytes)')
        with open(tmp, encoding='utf-8') as f:
            for i, line in enumerate(f):
                if i < 5:
                    print(f'  {line.rstrip()}')
    else:
        print('FAILED')
