"""_logger.py — Unified logging with auto-rotation for video-fetcher"""
import os, sys, tempfile, time, traceback
from datetime import datetime
from pathlib import Path

_LOG_DIR = None
_LOG_FILE = None
_LOG_HANDLE = None
_LOG_WARNED = False

_COLORS = {"info":"\033[36m","success":"\033[32m","warn":"\033[33m","error":"\033[31m","debug":"\033[90m"}
_RESET = "\033[0m"

def _warn(msg):
    """Emit a logging-subsystem warning without depending on _LOG_HANDLE."""
    global _LOG_WARNED
    if _LOG_WARNED:
        return
    _LOG_WARNED = True
    try:
        print(f"[logger] WARNING: {msg}", file=sys.stderr)
    except Exception:
        pass

def _get_log_dir():
    """Return a writable log dir. Never raises: logging must not kill the app."""
    global _LOG_DIR
    if _LOG_DIR is not None:
        return _LOG_DIR
    tried = []
    candidates = [Path(__file__).parent / "logs"]
    try:
        candidates.append(Path(tempfile.gettempdir()) / "video-fetcher-logs")
    except Exception:
        pass
    for cand in candidates:
        try:
            cand.mkdir(parents=True, exist_ok=True)
            probe = cand / ".write_test"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            _LOG_DIR = cand
            if cand != candidates[0]:
                _warn(f"primary log dir unavailable, using {cand}")
            return _LOG_DIR
        except Exception as e:
            tried.append(f"{cand} ({type(e).__name__}: {e})")
    _warn("no writable log dir; file logging disabled -> " + "; ".join(tried))
    return None

def set_log_file(name):
    global _LOG_FILE, _LOG_HANDLE
    if _LOG_HANDLE and _LOG_FILE:
        return  # already logging, don't overwrite
    log_dir = _get_log_dir()
    if log_dir is None:
        return  # already warned by _get_log_dir; stay silent-but-explicit
    date_str = datetime.now().strftime("%Y%m%d")
    today_file = log_dir / f"{name}_{date_str}.log"
    # Rotate if >5MB
    if today_file.exists() and today_file.stat().st_size > 5*1024*1024:
        for i in range(9,0,-1):
            src = log_dir / f"{name}_{date_str}_{i}.log"
            dst = log_dir / f"{name}_{date_str}_{i+1}.log"
            if src.exists():
                try: src.replace(dst)
                except: pass
        try: today_file.replace(log_dir / f"{name}_{date_str}_1.log")
        except: pass
    # Clean old (>10 files)
    existing = sorted(log_dir.glob(f"{name}_*.log"), reverse=True)
    for old in existing[9:]:
        try: old.unlink()
        except: pass
    try:
        _LOG_HANDLE = open(today_file, "a", encoding="utf-8")
    except Exception as e:
        _warn(f"cannot open log file {today_file} ({type(e).__name__}: {e}); file logging disabled")
        _LOG_HANDLE = None
        _LOG_FILE = None
        return
    _LOG_FILE = str(today_file)
    _LOG_HANDLE.write(f"\n{'='*60}\nvideo-fetcher log: {datetime.now().isoformat()}\npid={os.getpid()} platform={sys.platform}\n{'='*60}\n\n")
    _LOG_HANDLE.flush()

def log(level, msg, exc_info=False):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] [{level.upper():7s}] {msg}"
    color = _COLORS.get(level, "")
    stream = sys.stderr if level == "error" else sys.stdout
    # pythonw.exe intentionally provides no console streams. File logging must
    # continue without turning that normal GUI condition into a startup crash.
    if stream is not None:
        try:
            if stream.isatty():
                print(f"{color}{line}{_RESET}", file=stream)
            else:
                print(line, file=stream)
        except (AttributeError, OSError, ValueError):
            pass
    if _LOG_HANDLE:
        _LOG_HANDLE.write(f"{line}\n")
        if exc_info:
            _LOG_HANDLE.write(f"{traceback.format_exc()}\n")
        _LOG_HANDLE.flush()

def close():
    global _LOG_HANDLE, _LOG_FILE
    if _LOG_HANDLE:
        _LOG_HANDLE.write(f"\n{'='*60}\nlog ended: {datetime.now().isoformat()}\n{'='*60}\n")
        _LOG_HANDLE.close()
        _LOG_HANDLE = None
        _LOG_FILE = None
