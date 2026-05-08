import serial
import time
import threading
import pyautogui
from typing import Optional

SCENE_MAP = {
    "scene_1": "BRB",
    "scene_2": "LIVE",
}

# --- SERIAL CONFIG ---
SERIAL_PORT = '/dev/cu.usbserial-0001'        # change if needed
BAUD_RATE = 115200
SERIAL_TIMEOUT = 1

# --- OBS WEBSOCKET (optional) ---
# If you fill OBS_HOST/OBS_PORT/OBS_PASSWORD the module will attempt to use obs websocket
# Otherwise it will use OBS hotkeys (configure in OBS Settings -> Hotkeys)
USE_OBS_WEBSOCKET = True
OBS_HOST = "localhost"
OBS_PORT = 4455
OBS_PASSWORD = "KDZyPcn6kkHHYUPh"

# --- OBS HOTKEY FALLBACKS (only used when websocket not configured) ---
# Map logical scene names to key sequences (hotkeys you set in OBS)
OBS_HOTKEYS = {
    "scene_1": ("ctrl", "1"),
    "scene_2": ("ctrl", "2"),
}
# Mute/unmute OBS hotkey (if not using websocket)
OBS_MUTE_HOTKEY = ("ctrl", "shift", "m")

# --- Macro <-> action mapping (customize) ---
# Macros are simple strings you referenced in your UI mapping.
# Add entries to map macro name -> function call below.
# Example macros used: "emergency", "mute", "playpause", "obs_scene_1", "obs_scene_2"
# You can call run_macro("obs_scene_1") to switch scenes.
# Or run_macro("mute") to mute OBS (or press OBS mute hotkey).
# Or run_macro("playpause") to toggle Spotify (media play/pause).

# Optional obs websocket client; lazily created if enabled.
_obs_client = None

def _init_obs_client():
    global _obs_client
    if not USE_OBS_WEBSOCKET:
        return None
    try:
        # import here to avoid hard dependency if not used
        from obsws_python import ReqClient
        # OBS WebSocket v5 uses websocket protocol; ReqClient signature (host, port, password)
        _obs_client = ReqClient(host=OBS_HOST, port=OBS_PORT, password=OBS_PASSWORD)
        return _obs_client
    except Exception:
        _obs_client = None
        return None

def _switch_obs_scene(scene_name: str):
    """
    Switch OBS scene using WebSocket, fallback to hotkeys.
    """
    if USE_OBS_WEBSOCKET:
        try:
            client = _obs_client or _init_obs_client()
            if client:
                # map internal name -> actual OBS scene name
                actual_scene = SCENE_MAP.get(scene_name, scene_name)

                print("Switching to OBS scene:", actual_scene)  # DEBUG

                client.set_current_program_scene(actual_scene)
                return
        except Exception as e:
            print("OBS WebSocket error:", e)

    # fallback to hotkeys
    keys = OBS_HOTKEYS.get(scene_name)
    if keys:
        print("Fallback hotkey:", keys)
        pyautogui.hotkey(*keys)

def _toggle_obs_mute():
    if USE_OBS_WEBSOCKET:
        try:
            client = _obs_client or _init_obs_client()
            if client:
                # Toggle mute on the desired source (commonly 'Mic/Aux' or the name of your mixer source).
                # This requires changing SOURCE_NAME to match your OBS audio source name.
                SOURCE_NAME = "Mic/Aux"
                # This call expects a boolean or a toggle; using toggle method if available:
                client.toggle_mute(source_name=SOURCE_NAME)
                return
        except Exception:
            pass
    # fallback to global hotkey
    pyautogui.hotkey(*OBS_MUTE_HOTKEY)

from AppKit import NSSound

def _toggle_playpause():
    try:
        # This works more reliably with Spotify on macOS
        script = '''
        tell application "Spotify"
            playpause
        end tell
        '''
        import subprocess
        subprocess.run(["osascript", "-e", script])
    except Exception as e:
        print("Spotify AppleScript error:", e)

# Core: map macro string to actions
def run_macro(macro: str):
    print("RUNNING MACRO:", macro)
    macro = macro.strip().lower()
    if macro == "emergency":
        # example: mute obs, then play/pause spotify
        _toggle_obs_mute()
        _toggle_playpause()
    elif macro == "mute":
        _toggle_obs_mute()
    elif macro == "playpause":
        _toggle_playpause()
    elif macro.startswith("obs_scene_"):
        # expect macros like "obs_scene_scene_1" or "obs_scene_scene_2" etc.
        # strip prefix to get key to lookup
        scene_key = macro.replace("obs_scene_", "scene_")
        _switch_obs_scene(scene_key)
    else:
        # if macro looks like a hotkey string (e.g. "ctrl+shift+m" produced by recorder),
        # send it via pyautogui
        if "+" in macro:
            keys = macro.split("+")
            pyautogui.hotkey(*keys)
        else:
            # try single key press
            pyautogui.press(macro)

# Serial listener: watches for lines from ESP32 and triggers corresponding macro
def start_listener():
    def _listen_loop():
        try:
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=SERIAL_TIMEOUT)
            time.sleep(2)  # device warm-up
        except Exception as e:
            print("Serial open failed:", e)
            return

        print("Listening for ESP32 on", SERIAL_PORT)
        while True:
            try:
                if ser.in_waiting:
                    line = ser.readline().decode(errors="ignore").strip()
                    if not line:
                        continue
                    print("ESP32 ->", line)
                    # expected lines like "Gesture 1_DOWN" or "BTN3_DOWN" depending on firmware
                    # normalize to a mapping key
                    # you may want to map exact gestures to macros in your UI instead;
                    # here we assume mapping strings will be provided to run_macro by the UI layer
                    # for demo purposes we map a few known lines:
                    if line == "Gesture 1_DOWN" or line == "BTN1_DOWN":
                        run_macro("obs_scene_1") 
                    elif line == "Gesture 2_DOWN" or line == "BTN2_DOWN":
                        run_macro("obs_scene_2")
                    elif line == "Gesture 3_DOWN" or line == "BTN3_DOWN":
                        run_macro("playpause")
                    else:
                        # if line itself is a macro name, run it
                        run_macro(line)
            except Exception as e:
                print("Serial read error:", e)
                time.sleep(1)

    t = threading.Thread(target=_listen_loop, daemon=True)
    t.start()
