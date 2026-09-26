"""
_cdp_cookies.py — Zero-dep cookie extraction via Chrome DevTools Protocol
Used as fallback when file-based DPAPI decryption fails (Chrome/Edge v20
App-Bound Encryption).

Launches the browser with --remote-debugging-port, connects via raw WebSocket,
calls Storage.getCookies (browser decrypts cookies internally), then terminates.
"""
import os, json, time, socket, base64, struct, secrets, subprocess, tempfile

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


def _ws_recv(sock, timeout=10):
    """Read one complete WebSocket frame (server→client, unmasked)."""
    sock.settimeout(timeout)

    # Read first 2 bytes
    data = b''
    while len(data) < 2:
        chunk = sock.recv(2 - len(data))
        if not chunk:
            raise ConnectionError("WebSocket recv: connection closed")
        data += chunk

    opcode = data[0] & 0x0F
    masked = (data[1] & 0x80) != 0
    payload_len = data[1] & 0x7F

    if payload_len == 126:
        ext = b''
        while len(ext) < 2:
            ext += sock.recv(2 - len(ext))
        payload_len = struct.unpack('>H', ext)[0]
    elif payload_len == 127:
        ext = b''
        while len(ext) < 8:
            ext += sock.recv(8 - len(ext))
        payload_len = struct.unpack('>Q', ext)[0]

    mk = None
    if masked:
        mk = sock.recv(4)

    payload = b''
    while len(payload) < payload_len:
        chunk = sock.recv(min(payload_len - len(payload), 65536))
        if not chunk:
            break
        payload += chunk

    if masked and mk:
        payload = bytes(b ^ mk[i % 4] for i, b in enumerate(payload))

    return opcode, payload


# ──────────────────────────────────────────────────────────────
#  CDP helpers
# ──────────────────────────────────────────────────────────────

def _cdp_call(sock, _id, method, params=None, timeout=10):
    """Send a CDP command and return the result dict."""
    msg = {"id": _id, "method": method}
    if params:
        msg["params"] = params
    _ws_send(sock, json.dumps(msg, ensure_ascii=False))
    _, raw = _ws_recv(sock, timeout)
    return json.loads(raw)


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


def _kill_browser(proc):
    """Gracefully terminate browser process tree."""
    if proc is None:
        return
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

        # Wait for debugger endpoint
        deadline = time.time() + timeout
        ws_url = None
        while time.time() < deadline:
            # Check if browser crashed
            ret = proc.poll()
            if ret is not None:
                return False

            try:
                import urllib.request
                resp = urllib.request.urlopen(
                    f'http://127.0.0.1:{port}/json/version', timeout=2
                )
                info = json.loads(resp.read())
                ws_url = info.get('webSocketDebuggerUrl', '')
                if ws_url:
                    break
            except Exception:
                time.sleep(0.5)
                continue

        if not ws_url:
            return False

        # Connect WebSocket and extract cookies
        sock = _ws_connect(ws_url, timeout=10)

        try:
            # CDP: Storage.getCookies returns all cookies in all contexts
            result = _cdp_call(sock, 1, 'Storage.getCookies', timeout=15)
            cookies = result.get('result', {}).get('cookies', [])
        finally:
            try:
                sock.close()
            except Exception:
                pass

        if not cookies:
            return False

        # Write Netscape cookie file
        return _write_netscape(output_path, cookies)

    except Exception:
        return False
    finally:
        _kill_browser(proc)


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
