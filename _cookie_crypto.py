"""_cookie_crypto.py — Zero-dep Chromium cookie decryption via ctypes DPAPI + AES-GCM"""
import os, sys, json, base64, sqlite3, shutil, tempfile

_AES = None
def _init_aes():
    global _AES
    if _AES is not None: return _AES
    try: from cryptography.hazmat.primitives.ciphers.aead import AESGCM; _AES = 'cryptography'
    except ImportError:
        try: from Crypto.Cipher import AES; _AES = 'pycryptodome'
        except ImportError: _AES = None
    return _AES

def _aes_gcm_decrypt(key, ct):
    if len(ct) < 31: return None
    n, c, t = ct[3:15], ct[15:-16], ct[-16:]
    try:
        if _AES == 'cryptography':
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            return AESGCM(key).decrypt(n, c + t, None)
        elif _AES == 'pycryptodome':
            from Crypto.Cipher import AES
            return AES.new(key, AES.MODE_GCM, nonce=n).decrypt_and_verify(c, t)
    except Exception: return None

def _dpapi_decrypt(data):
    if sys.platform != 'win32': return None
    import ctypes; from ctypes import wintypes
    d, k = ctypes.windll.crypt32, ctypes.windll.kernel32
    class B(ctypes.Structure):
        _fields_ = [("c", wintypes.DWORD), ("p", ctypes.POINTER(ctypes.c_char))]
    ib = ctypes.create_string_buffer(data, len(data))
    ii, oo = B(len(data), ib), B()
    if d.CryptUnprotectData(ctypes.byref(ii), None, None, None, None, 0, ctypes.byref(oo)):
        r = ctypes.string_at(oo.p, oo.c); k.LocalFree(oo.p); return r
    return None

def _get_key(ls_path):
    try:
        with open(ls_path, encoding='utf-8') as f: ek = json.load(f).get('os_crypt', {}).get('encrypted_key', '')
        if not ek: return None
        raw = base64.b64decode(ek)
        return _dpapi_decrypt(raw[5:]) if raw[:5] == b'DPAPI' else _dpapi_decrypt(raw)
    except Exception: return None

BROWSERS = {
    'chrome': [('{L}/Google/Chrome/User Data',)], 'edge': [('{L}/Microsoft/Edge/User Data',)],
    'lenovo': [('{L}/Lenovo/SLBrowser/User Data',), ('{L}/Lenovo/SLB Browser/User Data',), ('{L}/Lenovo/LenovoBrowser/User Data',)],
    'brave': [('{L}/BraveSoftware/Brave-Browser/User Data',)], 'opera': [('{A}/Opera Software/Opera Stable',)],
}

def _find_db(key):
    L, A = os.environ.get('LOCALAPPDATA', ''), os.environ.get('APPDATA', '')
    for g in BROWSERS.get(key, []):
        base = g[0].replace('{L}', L).replace('{A}', A)
        if not os.path.isdir(base): continue
        ls = os.path.join(base, 'Local State')
        if not os.path.isfile(ls): continue
        for pn in ['Default'] + [f'Profile {i}' for i in range(1, 10)]:
            pd = os.path.join(base, pn)
            for cn in ['Network/Cookies', 'Cookies']:
                p = os.path.join(pd, cn)
                if os.path.isfile(p): return p, ls
    return None, None

def _read_db(db_path):
    conn, tmp = None, None
    try: conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        try:
            fd, tmp = tempfile.mkstemp(suffix='.sqlite', prefix='ck_')
            os.close(fd)
            shutil.copy2(db_path, tmp)
            conn = sqlite3.connect(tmp)
        except Exception: return None
    if not conn: return None
    try:
        return conn.execute("SELECT host_key, name, encrypted_value, path, expires_utc, is_secure FROM cookies WHERE encrypted_value IS NOT NULL AND length(encrypted_value)>0").fetchall()
    finally:
        try: conn.close()
        except: pass
        if tmp:
            try: os.unlink(tmp)
            except: pass

def export_cookies(browser_key, output_path):
    if _init_aes() is None: return False
    db, ls = _find_db(browser_key)
    if not db: return False
    key = _get_key(ls)
    if not key: return False
    rows = _read_db(db)
    if not rows: return False

    v10_cnt = v20_cnt = 0
    cnt = 0
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("# Netscape HTTP Cookie File\n# video-fetcher\n\n")
        for hk, nm, ev, ph, ex, sc in rows:
            if not ev: continue
            if ev[:3] == b'v20':
                v20_cnt += 1; continue  # v20 needs App-Bound Encryption (Chrome COM service)
            if ev[:3] != b'v10': continue
            v10_cnt += 1
            pl = _aes_gcm_decrypt(key, ev)
            if not pl: continue
            try: val = pl.decode('utf-8', errors='replace')
            except: continue
            flag = 'TRUE' if hk.startswith('.') else 'FALSE'
            exp = str(int(ex / 1000000 - 11644473600)) if ex else '0'
            f.write(f"{hk}\t{flag}\t{ph}\t{'TRUE' if sc else 'FALSE'}\t{exp}\t{nm}\t{val}\n")
            cnt += 1

    if v20_cnt > 0 and cnt == 0:
        # All cookies are v20 → can't decrypt with pure DPAPI
        return False
    return cnt > 0
