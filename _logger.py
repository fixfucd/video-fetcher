"""_logger.py — Unified logging with auto-rotation for video-fetcher"""
import os, sys, time, traceback
from datetime import datetime
from pathlib import Path

_LOG_DIR = None
_LOG_FILE = None
_LOG_HANDLE = None

_COLORS = {"info":"\033[36m","success":"\033[32m","warn":"\033[33m","error":"\033[31m","debug":"\033[90m"}
_RESET = "\033[0m"

def _get_log_dir():
    global _LOG_DIR
    if _LOG_DIR is None:
        _LOG_DIR = Path(__file__).parent / "logs"
        _LOG_DIR.mkdir(exist_ok=True)
    return _LOG_DIR

def set_log_file(name):
    global _LOG_FILE, _LOG_HANDLE
    if _LOG_HANDLE and _LOG_FILE:
        return  # already logging, don't overwrite
    log_dir = _get_log_dir()
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
    _LOG_FILE = str(today_file)
    _LOG_HANDLE = open(_LOG_FILE, "a", encoding="utf-8")
    _LOG_HANDLE.write(f"\n{'='*60}\nvideo-fetcher log: {datetime.now().isoformat()}\npid={os.getpid()} platform={sys.platform}\n{'='*60}\n\n")
    _LOG_HANDLE.flush()

def log(level, msg, exc_info=False):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] [{level.upper():7s}] {msg}"
    color = _COLORS.get(level, "")
    if sys.stdout.isatty():
        print(f"{color}{line}{_RESET}", file=sys.stderr if level=="error" else sys.stdout)
    else:
        print(line, file=sys.stderr if level=="error" else sys.stdout)
    if _LOG_HANDLE:
        _LOG_HANDLE.write(f"{line}\n")
        if exc_info:
            _LOG_HANDLE.write(f"{traceback.format_exc()}\n")
        _LOG_HANDLE.flush()

def close():
    global _LOG_HANDLE
    if _LOG_HANDLE:
        _LOG_HANDLE.write(f"\n{'='*60}\nlog ended: {datetime.now().isoformat()}\n{'='*60}\n")
        _LOG_HANDLE.close()
        _LOG_HANDLE = None
