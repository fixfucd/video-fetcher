import sys
sys.path.insert(0, r"C:\Users\lenovo\video-fetcher")
from fetch import BROWSER_CONFIG, FALLBACK_BROWSER_ORDER, get_alt_browsers, _expand_path

print("BROWSER_CONFIG keys:", list(BROWSER_CONFIG.keys()))
print("FALLBACK:", FALLBACK_BROWSER_ORDER)
print("alt for chrome:", get_alt_browsers("chrome"))
print("alt for edge:", get_alt_browsers("edge"))
print("alt for lenovo:", get_alt_browsers("lenovo"))
print()
for k, v in BROWSER_CONFIG.items():
    print(f"  {k}: native={v['native']}, yt_name={v['yt_name']}, priority={v['priority']}")
