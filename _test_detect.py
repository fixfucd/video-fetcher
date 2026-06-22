import sys, os
# Fix env vars for headless shell
os.environ.setdefault("LOCALAPPDATA", r"C:\Users\lenovo\AppData\Local")
os.environ.setdefault("APPDATA", r"C:\Users\lenovo\AppData\Roaming")
os.environ.setdefault("USERPROFILE", r"C:\Users\lenovo")

sys.path.insert(0, r"C:\Users\lenovo\video-fetcher")
from fetch import detect_installed_browsers, detect_browser_profiles, find_cookies_file, copy_cookies_db, get_available_browsers

print("=== detect_installed_browsers() ===")
installed = detect_installed_browsers()
for k, v in installed.items():
    status = f"[OK] ({v['profiles']} Profile)" if v["installed"] else "[--]"
    print(f"  {v['label']:12s} {status}")

print(f"\n=== get_available_browsers() ===")
avail = get_available_browsers()
print(f"  installed: {avail}")

print(f"\n=== detect_browser_profiles('chrome') ===")
profiles = detect_browser_profiles("chrome")
for prof_dir, name in profiles:
    print(f"  {name}: {prof_dir}")

print(f"\n=== find_cookies_file('chrome') ===")
path, pname = find_cookies_file("chrome")
print(f"  path={path}, profile={pname}")

if path:
    print(f"\n=== copy_cookies_db('chrome') ===")
    tmp = copy_cookies_db("chrome")
    if tmp:
        print(f"  temp file: {tmp} ({os.path.getsize(tmp)} bytes)")
        try:
            os.unlink(tmp)
            print("  cleaned up")
        except OSError:
            pass
    else:
        print("  copy failed (browser may be running)")
