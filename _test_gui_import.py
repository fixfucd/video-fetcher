import sys
sys.path.insert(0, r"C:\Users\lenovo\video-fetcher")

# 模拟 gui.py 的导入
from fetch import (
    PLATFORM_PRESETS, BROWSER_CONFIG, FALLBACK_BROWSER_ORDER,
    load_config, get_platform_presets,
    build_yt_dlp_args, check_tool, normalize_douyin_url,
    cleanup_temp_files, check_output_exists,
    find_cookies_file, get_browser_cookies_args,
    get_alt_browsers, is_cookie_lock_error, _expand_path,
)
print("gui.py 导入链: OK")
print("  find_cookies_file('lenovo'):", find_cookies_file("lenovo"))
print("  get_browser_cookies_args('chrome'):", get_browser_cookies_args("chrome"))
print("  get_browser_cookies_args('lenovo'):", get_browser_cookies_args("lenovo"))
print("  get_alt_browsers('chrome'):", get_alt_browsers("chrome"))
print("  is_cookie_lock_error('database is locked'):", is_cookie_lock_error("database is locked"))
print("  is_cookie_lock_error('normal output'):", is_cookie_lock_error("normal output"))
