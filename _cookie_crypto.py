"""_cookie_crypto.py — Zero-dep Chromium cookie decryption via ctypes DPAPI + AES-GCM"""
import os, sys, json, base64, hashlib, sqlite3, shutil, tempfile

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
        rows = conn.execute("SELECT host_key, name, encrypted_value, path, expires_utc, is_secure FROM cookies WHERE encrypted_value IS NOT NULL AND length(encrypted_value)>0").fetchall()
        try:
            version_row = conn.execute("SELECT value FROM meta WHERE key='version'").fetchone()
            version = int(version_row[0]) if version_row else None
        except (sqlite3.Error, TypeError, ValueError):
            # No usable meta row: report "unknown" instead of 0. Claiming an old
            # schema made every digest-prefixed value undecodable, and each one
            # was then dropped without a word.
            version = None
        return rows, version
    finally:
        try: conn.close()
        except: pass
        if tmp:
            try: os.unlink(tmp)
            except: pass

def _try_cdp_fallback(output_path, browser_key):
    """If local DPAPI decryption fails (v20 App-Bound Encryption), let the
    browser decrypt its own cookies via CDP."""
    try:
        from _cdp_cookies import export_cookies_cdp
        return export_cookies_cdp(output_path, browser_key=browser_key)
    except ImportError:
        return False


def _decode_cookie_value(host_key, plaintext, require_host_digest=None):
    """Decode Chromium v10 plaintext, including the schema-v24 host digest.

    New Chromium databases prefix every decrypted value with SHA256(host_key).
    ``require_host_digest`` selects the decoding rule:

      True  — the digest must be present (schema is known to be >= 24);
      False — the digest must be absent (older schema);
      None  — unknown schema: strip a matching digest when present, otherwise
              keep the value as-is.
    """
    host_digest = hashlib.sha256(host_key.encode('utf-8')).digest()
    if require_host_digest is None:
        if plaintext.startswith(host_digest):
            plaintext = plaintext[len(host_digest):]
    elif require_host_digest:
        if len(plaintext) < len(host_digest) or not plaintext.startswith(host_digest):
            return None
        plaintext = plaintext[len(host_digest):]
    try:
        value = plaintext.decode('utf-8')
        value.encode('latin-1')
        if any(char in value for char in ('\r', '\n', '\t', '\0')):
            return None
        return value
    except (UnicodeDecodeError, UnicodeEncodeError):
        return None


def export_cookies(browser_key, output_path):
    if _init_aes() is None: return False
    db, ls = _find_db(browser_key)
    if not db: return False
    key = _get_key(ls)
    if not key: return False
    db_result = _read_db(db)
    if not db_result: return False
    rows, db_version = db_result
    if not rows: return False
    # None means the schema version could not be read; let the decoder decide
    # per value instead of assuming an old schema.
    require_host_digest = None if db_version is None else db_version >= 24

    # Write beside the target and swap it in only once the export succeeded.
    # Opening the target directly used to truncate it first, so a failed export
    # destroyed the working cookies file that downloads depend on.
    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(output_path) + ".", suffix=".part",
        dir=os.path.dirname(os.path.abspath(output_path)) or None,
    )
    os.close(fd)

    decrypt_failed = unsupported = 0
    cnt = 0
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write("# Netscape HTTP Cookie File\n# video-fetcher\n\n")
            for hk, nm, ev, ph, ex, sc in rows:
                if not ev: continue
                if ev[:3] == b'v20':
                    unsupported += 1; continue  # v20 needs App-Bound Encryption (Chrome COM service)
                if ev[:3] != b'v10':
                    # Unknown scheme (e.g. a vendor-custom prefix). Not decryptable
                    # here; counted so the CDP fallback still triggers below.
                    unsupported += 1; continue
                pl = _aes_gcm_decrypt(key, ev)
                if not pl:
                    decrypt_failed += 1; continue
                val = _decode_cookie_value(hk, pl, require_host_digest=require_host_digest)
                if val is None:
                    decrypt_failed += 1; continue
                flag = 'TRUE' if hk.startswith('.') else 'FALSE'
                exp = str(int(ex / 1000000 - 11644473600)) if ex else '0'
                f.write(f"{hk}\t{flag}\t{ph}\t{'TRUE' if sc else 'FALSE'}\t{exp}\t{nm}\t{val}\n")
                cnt += 1
    except OSError:
        # Unwritable target or full disk: leave any existing file untouched.
        try: os.unlink(tmp_path)
        except OSError: pass
        return False

    if cnt <= 0:
        # Nothing usable was produced (App-Bound Encryption, unknown scheme, or
        # every value failed to decrypt) → let the browser decrypt its own
        # cookies via CDP, and keep the previous file if that fails too.
        try: os.unlink(tmp_path)
        except OSError: pass
        if unsupported or decrypt_failed:
            return _try_cdp_fallback(output_path, browser_key)
        return False

    try:
        os.replace(tmp_path, output_path)
    except OSError:
        try: os.unlink(tmp_path)
        except OSError: pass
        return False
    return True
