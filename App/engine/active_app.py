import sys
import subprocess

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes
    import psutil
elif sys.platform == "darwin":
    from AppKit import NSWorkspace


def get_active_window_info() -> tuple[str, str]:
    """Returns (process_name, window_title)"""
    try:
        if sys.platform == "win32":
            hwnd = ctypes.windll.user32.GetForegroundWindow()

            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value

            pid = ctypes.wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process = psutil.Process(pid.value).name()

            return process, title

        elif sys.platform == "darwin":
            app = NSWorkspace.sharedWorkspace().activeApplication()
            process = app.get("NSApplicationName", "")

            title = ""
            if "Chrome" in process:
                script = 'tell application "Google Chrome" to get title of active tab of front window'
            elif "Firefox" in process:
                script = 'tell application "Firefox" to get title of front window'
            elif "Safari" in process:
                script = 'tell application "Safari" to get name of front document'
            else:
                script = 'tell application "System Events" to get name of front window of (first process whose frontmost is true)'

            result = subprocess.run(["osascript", "-e", script],
                                    capture_output=True, text=True)
            title = result.stdout.strip()
            return process, title

        else:
            # Linux
            import psutil as _psutil
            pid = subprocess.check_output(
                ["xdotool", "getactivewindow", "getwindowpid"]
            ).decode().strip()
            process = _psutil.Process(int(pid)).name()
            title = subprocess.check_output(
                ["xdotool", "getactivewindow", "getwindowname"]
            ).decode().strip()
            return process, title

    except Exception as e:
        print(f"[active_app] error: {e}")
        return "", ""


def get_mapped_app() -> str:
    process, title = get_active_window_info()
    title_lower = title.lower()

    print(f"[active_app] process={repr(process)} title={repr(title)}")

    browser_processes = {
        "chrome.exe", "firefox.exe", "msedge.exe",
        "Google Chrome", "Firefox", "Safari", "Microsoft Edge"
    }

    if process in browser_processes:
        if "google slides" in title_lower:
            return "Google Slides"
        if "notion" in title_lower:
            return "Notion"
        return "Global"

    APP_NAME_MAP = {
        # Windows
        "figma.exe":       "Figma",
        "Photoshop.exe":   "Adobe Photoshop",
        "blender.exe":     "Blender",
        "Code.exe":        "Visual Studio Code",
        "slack.exe":       "Slack",
        "obs64.exe":       "OBS Studio",
        # macOS
        "Figma":           "Figma",
        "Adobe Photoshop": "Adobe Photoshop",
        "Blender":         "Blender",
        "Code":            "Visual Studio Code",
        "Slack":           "Slack",
        "OBS":             "OBS Studio",
    }

    return APP_NAME_MAP.get(process, "Global")