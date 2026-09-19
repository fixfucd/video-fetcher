import os, sys
from pathlib import Path

os.environ.setdefault("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
os.environ.setdefault("APPDATA", str(Path.home() / "AppData" / "Roaming"))
os.environ.setdefault("USERPROFILE", str(Path.home()))

# Resolve the project dir from this file's own location, not a hardcoded drive
# letter -- the project has lived on both E:\ and C:\ and absolute paths broke it.
PROJ = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJ))
from fetch import fetch

rc = fetch(
    "https://www.bilibili.com/video/BV1DQ7k6JE4P/",
    platform="bilibili",
    output_dir=str(PROJ / "downloads"),
)
sys.exit(rc)
