"""DialMatrix — the local web server behind the settings page.

Serves `revomate_studio.html` at http://127.0.0.1:8765 and provides:
  - GET  /api/state      : Read current device sector 0 (or latest backup .bin fallback)
  - POST /api/write      : Safely back up and write updated settings to device flash
  - POST /api/led-preview: show a colour on the device's LED for a moment (nothing is written)

Usage:
    python3 revomate_app.py
"""
import base64
import glob
import hashlib
import hmac
import secrets
import tempfile
from functools import wraps
import http.server
import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser

try:
    import hid
    from revomate_config import read_range, write_sector
except ImportError:
    hid = None
    read_range = None
    write_sector = None

import revomate_scripts
from revomate_device import device_operation, serialized_device_operation

VID, PID = 0x22EA, 0x004B
SECTOR_SIZE = 0x10000
FLASH_SECTORS = 32
_PLAN_KEY = secrets.token_bytes(32)
_PLAN_TTL = 10 * 60
DIAL_BASE, DIAL_MODE, DIAL_FUNC = 0x100, 0x60, 0x18
MODE_BASE, MODE_SIZE = 0x10, 0x20
SCRIPT_INFO = 0x10000
MEDIA = {
    "play": 10, "pause": 11, "stop": 12, "rec": 13, "fast_forward": 14,
    "rewind": 15, "next": 16, "previous": 17, "mute": 18,
    "volume_up": 19, "volume_down": 20,
}
SCRIPT_MEDIA = {name: code - 9 for name, code in MEDIA.items()}

# LED colours: the page works in sRGB hex, the device stores PWM duties (0..90).
# These two helpers must stay identical to decodeLedBytesToHex / encodeHexToLedBytes in revomate_studio.html.
LED_GAMMA = 2.0
LED_MAX_DUTY = 90
OFFICIAL_LED_PRESETS = [
    (0, 0, 0), (255, 255, 255), (255, 0, 0), (255, 120, 0), (255, 230, 0),
    (0, 255, 50), (0, 255, 255), (0, 0, 255), (255, 20, 255),
]


def _js_round(x):
    """Math.round() semantics (halves round up), so results match the page exactly."""
    import math
    return int(math.floor(x + 0.5))


def led_decode(b):
    if b is None or len(b) < 5:
        return "#00ff55"
    color_no, raw = b[0], (b[2], b[3], b[4])
    if 0 < max(raw) <= 180:
        def to_srgb(duty):
            norm = max(0.0, min(1.0, duty / LED_MAX_DUTY))
            return max(0, min(255, _js_round((norm ** (1 / LED_GAMMA)) * 255)))
        r, g, bl = (to_srgb(v) for v in raw)
        return f"#{r:02x}{g:02x}{bl:02x}"
    if 1 <= color_no <= 8:
        r, g, bl = OFFICIAL_LED_PRESETS[color_no]
        return f"#{r:02x}{g:02x}{bl:02x}"
    return "#000000"


def led_duties(hex_color):
    """'#rrggbb' -> (R, G, B) hardware duties, or None if the text is not a colour."""
    t = str(hex_color or "").lstrip("#")
    if len(t) != 6:
        return None
    try:
        r, g, b = int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)
    except ValueError:
        return None

    def from_srgb(v):
        norm = max(0.0, min(1.0, v / 255))
        return max(0, min(LED_MAX_DUTY, _js_round((norm ** LED_GAMMA) * LED_MAX_DUTY)))
    return from_srgb(r), from_srgb(g), from_srgb(b)


def led_apply(new, off, hex_color, label, changes):
    """Write one LED setting (8+ bytes at `off`) only if the colour really differs from what the device holds."""
    duties = led_duties(hex_color)
    if duties is None:
        return
    cur = bytes(new[off:off + 8])
    if led_decode(cur).lower() == str(hex_color).lower():
        return  # the page still shows exactly what the device holds
    want = bytearray(cur)
    want[0] = 0                      # colour number 0 = custom RGB (as in the factory image)
    if want[1] == 0xFF:
        want[1] = 0                  # detail flag: keep what the device has; factory custom colours use 0
    want[2], want[3], want[4] = duties
    if want[5] == 0xFF:
        want[5] = 0                  # brightness level: normal
    if bytes(want) == cur:
        return
    new[off:off + 8] = want
    changes.append(f"{label} LED: {cur.hex(' ')} → {bytes(want).hex(' ')}")


# The edition comes from a generated revomate_edition.py (packaged app) or from `--edition` when started from source.
try:
    from revomate_edition import EDITION
except ImportError:
    EDITION = "lite"
if "--edition" in sys.argv:
    EDITION = sys.argv[sys.argv.index("--edition") + 1]
HOME_PAGE = "/revomate_studio.html"
PORT = int(os.environ.get("REVOMATE_PORT", "8765"))
HERE = os.path.dirname(os.path.abspath(__file__))
# Packaged as a .app (PyInstaller): the page lives inside the bundle (read-only), so backups go to the user's Documents folder.
if getattr(sys, "frozen", False):
    HERE = getattr(sys, "_MEIPASS", HERE)
    # an existing folder from the earlier name (Rev-O-mate Studio) is kept in use: nothing is moved or renamed
    _docs = os.path.join(os.path.expanduser("~"), "Documents")
    if sys.platform == "win32":
        # the real Documents folder (it can live inside OneDrive, not under the home folder)
        try:
            import ctypes
            from ctypes import wintypes
            _buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, _buf) == 0 and _buf.value:     # 5 = CSIDL_PERSONAL
                _docs = _buf.value
        except Exception:
            pass
    DATA_DIR = os.path.join(_docs, "DialMatrix バックアップ")
    if not os.path.isdir(DATA_DIR) and os.path.isdir(os.path.join(_docs, "Rev-O-mate Studio バックアップ")):
        DATA_DIR = os.path.join(_docs, "Rev-O-mate Studio バックアップ")
    os.makedirs(DATA_DIR, exist_ok=True)
else:
    DATA_DIR = HERE

MOD_KEYCODES = {
    "lctrl": 0xE0,
    "lshift": 0xE1,
    "lalt": 0xE2,
    "lgui": 0xE3,
}


def try_open_vendor():
    """Return hid.device() if Rev-O-mate vendor interface is accessible, else None."""
    try:
        for info in hid.enumerate(VID, PID):
            if info.get("usage_page") == 0xFF00:
                d = hid.device()
                d.open_path(info["path"])
                return d
    except Exception:
        pass
    return None


def latest_sector0_backup():
    files = glob.glob(os.path.join(DATA_DIR, "revomate_sector0_backup_*.bin"))
    for pattern in ("revomate_before_write_*.bin", "revomate_full_backup_*.bin"):
        files.extend(glob.glob(os.path.join(DATA_DIR, pattern)))
    files = [path for path in files if os.path.getsize(path) in (SECTOR_SIZE, SECTOR_SIZE * FLASH_SECTORS)]
    return max(files, key=lambda path: os.stat(path).st_mtime_ns) if files else None


def build_script_bytecode(sw_obj, target_size):
    """Build script bytecode of exact length `target_size` for media or keyboard shortcut."""
    role = sw_obj.get("role")
    if role == "media":
        media_name = sw_obj.get("media", "play")
        t = SCRIPT_MEDIA.get(media_name, 1)
        body = [0x43, t, 0x70, 0x00, 0x01, 0x42, t]
        pad_pair = [0x42, t]
    elif role == "key":
        key = int(sw_obj.get("key", 0)) & 0xFF
        mods = [MOD_KEYCODES[m] for m in sw_obj.get("mods", []) if m in MOD_KEYCODES]
        press_seq = []
        for mk in mods:
            press_seq += [0x41, mk]
        if key != 0:
            press_seq += [0x41, key, 0x70, 0x00, 0x01, 0x40, key]
            pad_pair = [0x40, key]
        elif mods:
            press_seq += [0x70, 0x00, 0x01]
            pad_pair = [0x40, mods[0]]
        else:
            return None
        for mk in reversed(mods):
            press_seq += [0x40, mk]
        body = press_seq
    else:
        return None

    if len(body) > target_size:
        raise ValueError(f"Script size ({target_size}B) is too short for {len(body)}B sequence")
    rest = target_size - len(body)
    if rest % 2 == 1:
        body += [0x70, 0x00, 0x01]
        rest -= 3
    if rest < 0:
        raise ValueError(f"Script size ({target_size}B) cannot be padded cleanly")
    body += pad_pair * (rest // 2)
    assert len(body) == target_size
    return bytes(body)




SIMPLE_TYPES = {0, *range(1, 9), 9, *range(10, 21)}  # none, mouse(1..8), keyboard key(9), media types(10..20)
MOUSE_CLICK_MASKS = {1: 0x01, 2: 0x02, 3: 0x04, 4: 0x08, 5: 0x10, 6: 0x01}


# Button numbers (SW1..SW11) -> the codes shown on the page's preview (R1..R3, B1..B4, L3..L1, C1)
def push_runtime_settings(dev, sec0):
    """Tell the running firmware about the new settings (the vendor app does the same after saving),
    so that most changes work without unplugging the USB cable. Nothing here is written to flash.

    Layouts (from the vendor app, Form1.cs): 0x36 per mode, 0x38 per mode and dial function, 0x3C per mode and button.
    Returns the number of commands the device acknowledged.
    """
    def send(cmd, payload):
        buf = [0, cmd, *payload]
        buf += [0xFF] * (65 - len(buf))
        dev.write(buf)
        for _ in range(5):
            r = dev.read(64, 500)
            if r and r[0] == cmd:
                if r[1] != 0:
                    raise IOError(f"本体が受け付けませんでした (cmd={cmd:#x})")
                return
        raise IOError(f"本体から返事がありません (cmd={cmd:#x})")

    sent = 0
    for m in range(3):
        mb = MODE_BASE + MODE_SIZE * m
        # 0x36: mode no, 11 script numbers, 11 special functions, default dial function, LED colour (no, detail, R, G, B, level)
        send(0x36, [m, *sec0[mb:mb + 0x1D]])
        sent += 1
        for f in range(4):
            a = DIAL_BASE + DIAL_MODE * m + DIAL_FUNC * f
            # 0x38: mode no, function no, CW(8), CCW(8), LED colour (no, detail, R, G, B, level)
            send(0x38, [m, f, *sec0[a:a + 0x16]])
            sent += 1
        for sw in range(11):
            o = 0xB00 + 0x58 * m + 8 * sw
            # 0x3C: mode no, button no, 8 bytes of button function data
            send(0x3C, [m, sw, *sec0[o:o + 8]])
            sent += 1
    return sent


SW_CODE = {i: str(i) for i in range(1, 12)}


def button_label(m_idx, s_idx):
    return f"モード{m_idx + 1} ボタン{SW_CODE.get(s_idx + 1, str(s_idx + 1))}"


def apply_changes(sec0, cfg, ready=None):
    """Return (new sector 0, list of changes, notes), touching only what the page changed.

    Everything the page cannot represent (script-type dial actions, wheel amounts, LED bytes,
    existing special functions, button scripts) is left exactly as it is on the device.
    """
    if all(b == 0xFF for b in sec0[DIAL_BASE:DIAL_BASE + 8]):
        raise ValueError("本体の設定が空です。先に初期化/復元してください。")
    new = bytearray(sec0)
    changes = []
    modes = cfg.get("modes", [])
    for m_idx in range(min(3, len(modes))):
        m_obj = modes[m_idx]
        mb = MODE_BASE + MODE_SIZE * m_idx

        want = int(m_obj.get("defaultDialFunc", new[mb + 0x16]))
        if 0 <= want < 4 and want != new[mb + 0x16]:
            changes.append(f"モード{m_idx + 1}: 起動時のダイヤル {new[mb + 0x16] + 1} → {want + 1}")
            new[mb + 0x16] = want

        for f_idx, df in enumerate(m_obj.get("dialFuncs", [])[:4]):
            a = DIAL_BASE + DIAL_MODE * m_idx + DIAL_FUNC * f_idx
            for off, side in ((0, "cw"), (8, "ccw")):
                act = df.get(side, {})
                t = int(act.get("type", 0)) & 0xFF
                cur = bytes(new[a + off:a + off + 8])
                if t == 9:
                    mod, key = int(act.get("mod", 0)) & 0xFF, int(act.get("key", 0)) & 0xFF
                    same = cur[0] == 9 and cur[1] == mod and cur[2] == key
                    val_bytes = bytes([t, mod, key, 0, 0, 0, 0])
                elif 1 <= t <= 8:
                    if t == 8:
                        scroll = 255 if (act.get("scroll") == 255 or act.get("mouse") == "wheel_down") else 1
                        same = cur[0] == 8 and cur[4] == scroll
                        val_bytes = bytes([t, 0, 0, 0, scroll, 0, 0])
                    else:
                        mask = MOUSE_CLICK_MASKS.get(t, 0x01)
                        same = cur[0] == t and cur[1] == mask
                        val_bytes = bytes([t, mask, 0, 0, 0, 0, 0])
                else:
                    same = cur[0] == t
                    val_bytes = bytes([t, 0, 0, 0, 0, 0, 0])
                side_label = '右回転' if side == 'cw' else '左回転'
                if not (same or t not in SIMPLE_TYPES):   # same = unchanged; non-simple = something the page cannot express
                    sens = cur[7] if cur[0] in SIMPLE_TYPES else 0x32
                    new[a + off:a + off + 8] = val_bytes + bytes([sens])
                    changes.append(f"モード{m_idx + 1} ダイヤル{f_idx + 1} {side_label}: {cur.hex(' ')} → {bytes(new[a + off:a + off + 8]).hex(' ')}")
                # Rotation speed ("sensitivity", 1..100) is the last byte of the 8-byte action
                want = df.get("sensCw" if side == "cw" else "sensCcw", df.get("sens"))
                try:
                    want = int(want)
                except (TypeError, ValueError):
                    want = None
                have = new[a + off + 7]
                if want is not None and 1 <= want <= 100 and new[a + off] in SIMPLE_TYPES and 1 <= have <= 100 and have != want:
                    new[a + off + 7] = want
                    changes.append(f"モード{m_idx + 1} ダイヤル{f_idx + 1} {side_label}の回転スピード: {have} → {want}")

        for s_idx, sw_obj in enumerate(m_obj.get("switches", [])[:11]):
            if sw_obj.get("role") != "special":
                # A button the user edited on the page and turned into a normal action (media / key / ...):
                # if the device still has a special function (mode switch, hold-dial...) on it, release it.
                held = new[mb + 11 + s_idx]
                if sw_obj.get("scriptDirty") and sw_obj.get("role") == "none" and (ready is None or (m_idx, s_idx) in ready):
                    # 「未設定」: the button's script number goes back to 0 (the script itself stays in the flash, unused)
                    if new[mb + s_idx] not in (0, 0xFF):
                        changes.append(f"{button_label(m_idx, s_idx)}: スクリプト {new[mb + s_idx]} → 未設定（0）")
                        new[mb + s_idx] = 0
                if sw_obj.get("scriptDirty") and held != 0 and (ready is None or (m_idx, s_idx) in ready):
                    changes.append(f"{button_label(m_idx, s_idx)}: 特殊機能 {held} → 0（本体機能切替をやめて、ボタン本来の動作に戻します）")
                    new[mb + 11 + s_idx] = 0
                continue
            want = int(sw_obj.get("special", 0)) & 0xFF
            if want != new[mb + 11 + s_idx]:
                changes.append(f"{button_label(m_idx, s_idx)}: 特殊機能 {new[mb + 11 + s_idx]} → {want}")
                new[mb + 11 + s_idx] = want

        # LED colours: the mode's standby colour and each dial function's colour
        led_apply(new, mb + 0x17, m_obj.get("baseLed"), f"モード{m_idx + 1} 待機", changes)
        for f_idx, df in enumerate(m_obj.get("dialFuncs", [])[:4]):
            a = DIAL_BASE + DIAL_MODE * m_idx + DIAL_FUNC * f_idx
            led_apply(new, a + 0x10, df.get("led"), f"モード{m_idx + 1} ダイヤル{f_idx + 1}", changes)

    notes = ["回転スピードは、対応する動作で本体の値が1〜100のときだけ変更します。共有スクリプトや未対応の動作は変更しません。"]
    return new, changes, notes


MEDIA_BY_SCRIPT = {v: k for k, v in SCRIPT_MEDIA.items()}
# Mouse steps in a script (vendor app, SCRIPT_COMMAND_*): one byte each, press / release
MOUSE_BUTTON_OPS = {"lclick": (0x29, 0x21), "rclick": (0x2A, 0x22), "mclick": (0x2C, 0x24), "b4click": (0x2D, 0x25), "b5click": (0x2E, 0x26)}
MOUSE_PRESS_TO_ID = {v[0]: k for k, v in MOUSE_BUTTON_OPS.items()}
MOUSE_RELEASE_OPS = {v[1] for v in MOUSE_BUTTON_OPS.values()}
WHEEL_UP_OP, WHEEL_DOWN_OP = 0x31, 0x32    # followed by one byte (01 = up, FF = down)
MOD_NAMES = {0xE0: "lctrl", 0xE1: "lshift", 0xE2: "lalt", 0xE3: "lgui", 0xE4: "lctrl", 0xE5: "lshift", 0xE6: "lalt", 0xE7: "lgui"}


def decode_button_script(body, mode=0):
    """Read a button script back into what the page can show.

    Returns {"role": "media", "media": name, "hold": bool}, {"role": "key", "key": code, "mods": [...], "hold": bool},
    {"role": "mouse", "mouse": id, "hold": bool} or {"role": "macro"} (several different steps, unknown steps...).
    Opcodes: 41/40 key press/release, 43/42 media press/release, 29.. mouse button press / 21.. release, 31/32 wheel, 70 hh ll wait.
    """
    i, n = 0, len(body)
    pressed, medias, mouse_presses, wheels = [], [], [], []
    releases = 0
    try:
        while i < n:
            op = body[i]
            if op in (0x41, 0x40, 0x43, 0x42):
                v = body[i + 1]
                if op == 0x41 and v not in pressed:
                    pressed.append(v)
                if op == 0x43 and v not in medias:
                    medias.append(v)
                if op in (0x40, 0x42):
                    releases += 1
                i += 2
            elif op in MOUSE_PRESS_TO_ID:
                mouse_presses.append(MOUSE_PRESS_TO_ID[op])
                i += 1
            elif op in MOUSE_RELEASE_OPS:
                releases += 1
                i += 1
            elif op in (WHEEL_UP_OP, WHEEL_DOWN_OP):
                wheels.append((op, body[i + 1]))
                i += 2
            elif op == 0x70:
                i += 3
            else:
                return {"role": "macro"}
    except IndexError:
        return {"role": "macro"}
    hold = mode in (2, 3) or releases == 0     # runs while the button is held (or never releases its keys)
    kinds = sum(1 for x in (pressed, medias, mouse_presses, wheels) if x)
    if kinds != 1:
        return {"role": "macro"}
    if medias:
        if len(medias) == 1 and medias[0] in MEDIA_BY_SCRIPT:
            return {"role": "media", "media": MEDIA_BY_SCRIPT[medias[0]], "hold": hold}
        return {"role": "macro"}
    if mouse_presses:
        first = mouse_presses[0]
        if any(m != first for m in mouse_presses) or len(mouse_presses) > 2:
            return {"role": "macro"}
        if len(mouse_presses) == 2:
            return {"role": "mouse", "mouse": "dclick", "hold": False} if first == "lclick" else {"role": "macro"}
        return {"role": "mouse", "mouse": first, "hold": hold}
    if wheels:
        if len(wheels) == 1 and wheels[0] == (WHEEL_UP_OP, 0x01):
            return {"role": "mouse", "mouse": "wheel_up", "hold": False}
        if len(wheels) == 1 and wheels[0] == (WHEEL_DOWN_OP, 0xFF):
            return {"role": "mouse", "mouse": "wheel_down", "hold": False}
        return {"role": "macro"}
    mods, keys = [], []
    for k in pressed:
        if k in MOD_NAMES:
            if MOD_NAMES[k] not in mods:
                mods.append(MOD_NAMES[k])
        else:
            keys.append(k)
    if len(keys) <= 1:
        return {"role": "key", "key": keys[0] if keys else 0, "mods": mods, "hold": hold}
    return {"role": "macro"}


def decode_all_buttons(read, sec0):
    """For every button that runs a script (and has no special function), decode the script. Returns a list of items."""
    items = []
    info = read(revomate_scripts.INFO_ADDR, revomate_scripts.SECTOR_SIZE)
    records = revomate_scripts.read_records(info)
    for m in range(3):
        mb = MODE_BASE + MODE_SIZE * m
        for s_idx in range(11):
            n = sec0[mb + s_idx]
            special = sec0[mb + 11 + s_idx]
            if special not in (0, 0xFF):
                continue
            if n in (0, 0xFF) or n not in records:
                items.append({"mode": m, "sw": s_idx + 1, "role": "none"})
                continue
            rec = records[n]
            item = decode_button_script(bytes(read(rec["addr"], rec["size"])), rec.get("mode", 0))
            item.update({"mode": m, "sw": s_idx + 1})
            items.append(item)
    return items


HOLD_MODE = 3        # script mode "hold": the keys stay pressed while the button is held (vendor app: SCRIPT_MODE_HOLD_MODE)


def build_button_script(sw):
    """(script bytes, script mode) for a button edited on the page (media key or keyboard shortcut); None if not supported.

    Normal: shapes copied from the factory scripts. A plain key is `41 key 70 00 01 40 key`; with modifiers the modifiers
    are pressed first, then `70 00 0a`, the key, and everything is released in reverse (as the factory Ctrl+S script).
    Hold ("押している間だけ"): the same presses but no releases, with the script mode set to hold.
    """
    role = sw.get("role")
    hold = bool(sw.get("hold"))
    if role == "media":
        t = SCRIPT_MEDIA.get(sw.get("media"))
        if t is None:
            return None
        if hold:
            return bytes([0x43, t, 0x70, 0x00, 0x01]), HOLD_MODE
        return bytes([0x43, t, 0x70, 0x00, 0x01, 0x42, t]), 0
    if role == "mouse":
        mid = sw.get("mouse")
        if mid == "wheel_up":
            return bytes([WHEEL_UP_OP, 0x01]), 0
        if mid == "wheel_down":
            return bytes([WHEEL_DOWN_OP, 0xFF]), 0
        if mid == "dclick":
            press, release = MOUSE_BUTTON_OPS["lclick"]
            click = [press, 0x70, 0x00, 0x01, release]
            return bytes(click + [0x70, 0x00, 0x32] + click), 0
        if mid in MOUSE_BUTTON_OPS:
            press, release = MOUSE_BUTTON_OPS[mid]
            if hold:
                return bytes([press, 0x70, 0x00, 0x01]), HOLD_MODE
            return bytes([press, 0x70, 0x00, 0x01, release]), 0
        return None
    if role == "key":
        key = int(sw.get("key", 0)) & 0xFF
        mods = [MOD_KEYCODES[m] for m in sw.get("mods", []) if m in MOD_KEYCODES]
        if key == 0 and not mods:
            return None
        body = []
        for m in mods:
            body += [0x41, m]
        if key:
            if mods:
                body += [0x70, 0x00, 0x0A]
            body += [0x41, key, 0x70, 0x00, 0x01]
            if not hold:
                body += [0x40, key]
                if mods:
                    body += [0x70, 0x00, 0x01]
        else:
            body += [0x70, 0x00, 0x01]
        if not hold:
            for m in reversed(mods):
                body += [0x40, m]
        return bytes(body), (HOLD_MODE if hold else 0)
    return None


def plan_script_rewrites(read, sec0, cfg):
    """Plan the button scripts (media keys and keyboard shortcuts) that the user edited on the page.

    `read(addr, size)` returns flash bytes. Only buttons the page marked `scriptDirty` are touched, only when the
    device already has a script for them, and never when that script is shared with another button.
    The script may get longer or shorter: the scripts of its sector are re-packed the way the vendor app does
    (see revomate_scripts.py). Returns (wanted, notes, ready): `wanted` holds {label, script_no, body, old} for scripts that
    must change; `ready` is the set of (mode, button) whose edited action is, or will be, correctly in the device.
    """
    wanted, notes, ready, assign = [], [], set(), {}
    if not any(sw.get("scriptDirty") for m in cfg.get("modes", [])[:3] for sw in m.get("switches", [])[:11]):
        return wanted, notes, ready, assign   # nothing edited: do not even read the script table
    info = read(revomate_scripts.INFO_ADDR, revomate_scripts.SECTOR_SIZE)
    records = revomate_scripts.read_records(info)
    refs = {}
    for m in range(3):
        mb = MODE_BASE + MODE_SIZE * m
        for s_idx in range(11):
            n = sec0[mb + s_idx]
            if n not in (0, 0xFF):
                refs.setdefault(n, []).append((m, s_idx))

    for m_idx, m_obj in enumerate(cfg.get("modes", [])[:3]):
        mb = MODE_BASE + MODE_SIZE * m_idx
        for s_idx, sw in enumerate(m_obj.get("switches", [])[:11]):
            if not sw.get("scriptDirty"):
                continue
            role = sw.get("role")
            label = button_label(m_idx, s_idx)
            if role == "special":
                ready.add((m_idx, s_idx))
                continue  # handled as a special function in apply_changes()
            if role == "none":
                ready.add((m_idx, s_idx))   # cleared in apply_changes(): the button's script number and special function become 0
                continue
            if role not in ("media", "key", "mouse"):
                notes.append(f"{label}: 書き込める動作ではありません。")
                continue
            built = build_button_script(sw)
            body, script_mode = built if built else (None, 0)
            if body is None:
                notes.append(f"{label}: 書き込める動作ではありません（キーが選ばれていないなど）。")
                continue
            n = sec0[mb + s_idx]
            if n in (0, 0xFF) or n not in records:
                # The button has no script yet: create one under the vendor's numbering (150 + 11*mode + button)
                taken = {**records, **{w["script_no"]: None for w in wanted}}
                new_no = revomate_scripts.pick_free_number(taken, revomate_scripts.button_script_number(m_idx, s_idx))
                if new_no is None:
                    notes.append(f"{label}: スクリプトの空き番号がないため、書き込めません。")
                    continue
                wanted.append({"label": label, "script_no": new_no, "body": body, "mode": script_mode, "old": b"", "new": True})
                assign[(m_idx, s_idx)] = new_no
                ready.add((m_idx, s_idx))
                continue
            if len(refs.get(n, [])) > 1:
                notes.append(f"{label}: 他のボタンと同じスクリプト（No.{n}）を共有しているため、書き込みませんでした。")
                continue
            rec = records[n]
            cur = bytes(read(rec["addr"], rec["size"]))
            ready.add((m_idx, s_idx))
            if cur == body and rec.get("mode") == script_mode:
                continue
            wanted.append({"label": label, "script_no": n, "body": body, "mode": script_mode, "old": cur})
    return wanted, notes, ready, assign


def compute_script_writes(read, wanted):
    """Apply every wanted script one after another (in memory) and return ({sector: new bytes}, [info lines])."""
    overlay = {}

    def view(addr, n):
        sector = addr // revomate_scripts.SECTOR_SIZE
        if sector in overlay:
            off = addr % revomate_scripts.SECTOR_SIZE
            return bytes(overlay[sector][off:off + n])
        return bytes(read(addr, n))

    info = bytes(read(revomate_scripts.INFO_ADDR, revomate_scripts.SECTOR_SIZE))
    lines = []
    for w in wanted:
        if w.get("new"):
            plan = revomate_scripts.plan_insert(info, view, w["script_no"], w["body"], w.get("mode", 0))
        else:
            plan = revomate_scripts.plan_replace(info, view, w["script_no"], w["body"], w.get("mode", 0))
        overlay[plan["sector_index"]] = plan["sector_data"]
        info = plan["info_data"]
        overlay[1] = info
        if w.get("new"):
            lines.append(f"{w['label']}: 新しいスクリプト{w['script_no']}を作成 ({len(w['body'])}B{'・押している間' if w.get('mode') == HOLD_MODE else ''}) {w['body'].hex(' ')}")
        else:
            lines.append(f"{w['label']}: スクリプト{w['script_no']} ({len(w['old'])}B → {len(w['body'])}B{'・押している間' if w.get('mode') == HOLD_MODE else ''}) "
                         f"{w['old'].hex(' ')} → {w['body'].hex(' ')}")
    return overlay, lines


def _led_module():
    """The LED code of this edition (preview / pause / resume)."""
    import revomate_ledpreview as led
    return led


def _serialize_device_request(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        paths = {"/api/state", "/api/write", "/api/led-preview"}
        # LED commands take the shared lock themselves; do not mark them busy.
        if self.path in paths and self.path not in {"/api/led-preview", "/api/led-demo"}:
            with device_operation():
                return method(self, *args, **kwargs)
        return method(self, *args, **kwargs)
    return wrapped


class StudioHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    def log_message(self, fmt, *args):
        pass  # Keep console output clean

    def _send_json(self, obj, status=200):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json_body(self, length):
        """The request's JSON object; a malformed or non-object body is read as empty."""
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    @_serialize_device_request
    def do_GET(self):
        if not self._local_request_ok():
            return self._send_json({"ok": False, "error": "forbidden"}, 403)
        if self.path == "/" or self.path.startswith("/?"):
            self.path = HOME_PAGE
            return super().do_GET()


        if self.path == "/api/sound":
            import revomate_sound
            return self._send_json({"ok": True, "prefs": revomate_sound.load(EDITION), "sounds": revomate_sound.sounds()})

        if self.path == "/api/licenses":
            for d in (HERE, os.path.dirname(os.path.abspath(__file__))):
                lp = os.path.join(d, "THIRD_PARTY_NOTICES.md")
                if os.path.exists(lp):
                    with open(lp, encoding="utf-8") as f:
                        return self._send_json({"ok": True, "text": f.read()})
            return self._send_json({"ok": False, "error": "no notices"}, 404)

        if self.path == "/api/ping":
            PING["last"] = time.time()
            return self._send_json({"ok": True, "packaged": bool(getattr(sys, "frozen", False))})

        if self.path == "/api/state":
            dev = try_open_vendor()
            if dev is not None:
                try:
                    sec0 = read_range(dev, 0, SECTOR_SIZE)
                    decoded = None
                    try:
                        decoded = decode_all_buttons(lambda a, n: read_range(dev, a, n), sec0)
                    except Exception:
                        decoded = None      # the settings are still shown; only the button actions are left as they were
                    dev.close()
                    return self._send_json({
                        "ok": True,
                        "source_label": "実機接続中 (USB Vendor HID)",
                        "sector0_hex": sec0.hex(),
                        "switches_decoded": decoded,
                    })
                except Exception as e:
                    try:
                        dev.close()
                    except Exception:
                        pass

            fb = latest_sector0_backup()
            if fb:
                sec0 = open(fb, "rb").read(SECTOR_SIZE)
                return self._send_json({
                    "ok": True,
                    "source_label": f"バックアップ表示中 ({os.path.basename(fb)})",
                    "sector0_hex": sec0.hex(),
                })

            return self._send_json({"ok": False, "error": "No device or backup found"}, 404)

        if self.path.split("?")[0] != HOME_PAGE:
            return self._send_json({"ok": False, "error": "not found"}, 404)  # don't serve backups etc.
        return super().do_GET()

    def _local_request_ok(self, post=False):
        """Reject requests that did not come from this page on this machine (CSRF / DNS rebinding)."""
        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
            return False
        if post:
            origin = self.headers.get("Origin", "")
            if origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
                return False
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return False
        return True

    @_serialize_device_request
    def do_POST(self):
        if not self._local_request_ok(post=True):
            return self._send_json({"ok": False, "error": "forbidden"}, 403)

        if self.path == "/api/save-file":
            length = int(self.headers.get("Content-Length", "0"))
            if length > 12 * 1024 * 1024:
                return self._send_json({"ok": False, "error": "too large"}, 413)
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except ValueError:
                return self._send_json({"ok": False, "error": "bad request"}, 400)
            if not isinstance(body, dict):
                return self._send_json({"ok": False, "error": "bad request"}, 400)
            name = os.path.basename(str(body.get("name", "export")))
            if os.path.splitext(name)[1].lower() not in (".json", ".bin"):
                return self._send_json({"ok": False, "error": "bad file type"}, 400)
            data = base64.b64decode(body["b64"]) if "b64" in body else str(body.get("text", "")).encode("utf-8")
            path = None
            if ASK_SAVE_PATH is not None and body.get("ask", True):
                path = ASK_SAVE_PATH(name)                    # the person chooses where to save
                if not path:
                    return self._send_json({"ok": True, "cancelled": True})
                if os.path.splitext(path)[1].lower() not in (".json", ".bin"):
                    path += os.path.splitext(name)[1]
            else:
                folder = os.path.join(DATA_DIR, "書き出し")
                os.makedirs(folder, exist_ok=True)
                path, n = os.path.join(folder, name), 1
                while os.path.exists(path):
                    root, ext = os.path.splitext(name)
                    path, n = os.path.join(folder, f"{root}_{n}{ext}"), n + 1
            with open(path, "wb") as f:
                f.write(data)
            try:
                if sys.platform == "win32":
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])      # show it in Explorer
                else:
                    subprocess.Popen(["open", "-R", path])      # show it in Finder
            except Exception:
                pass
            return self._send_json({"ok": True, "path": path})
        if self.path in ("/api/sound", "/api/sound-play"):
            import revomate_sound
            length = int(self.headers.get("Content-Length", "0"))
            try:
                body = self._read_json_body(length)
            except ValueError:
                return self._send_json({"ok": False, "error": "bad request"}, 400)
            if self.path == "/api/sound-play":
                return self._send_json({"ok": revomate_sound.play(str(body.get("name", "")))})
            return self._send_json({"ok": True, "prefs": revomate_sound.save(body, EDITION)})
        if self.path == "/api/led-preview":
            length = int(self.headers.get("Content-Length", "0"))
            body = self._read_json_body(length)
            color = str(body.get("color", ""))
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                return self._send_json({"ok": False, "error": "bad colour"}, 400)
            try:
                shown = _led_module().preview(color, int(body.get("ms", 500)))
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)}, 500)
            return self._send_json({"ok": True, "shown": bool(shown)})

        if self.path == "/api/quit":
            self._send_json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        write_paths = ("/api/write",)
        if self.path in write_paths:
            length = int(self.headers.get("Content-Length", "0"))
            body = self._read_json_body(length)
            # The request-level transaction owns the device until close, with no
            # timeout that could expire during a long backup or be reset by preview.
            dev = try_open_vendor()
            if dev is None:
                return self._send_json({
                    "ok": False,
                    "error": "Rev-O-mate (vendor interface 0xFF00) が見つからないか、権限がありません。",
                }, 400)
            try:
                if self.path == "/api/write":
                    out = write_config(dev, body.get("configState", {}), dry_run=bool(body.get("dry_run")),
                                       plan_token=body.get("plan_token"))
                dev.close()
                return self._send_json(out)
            except Exception as e:
                try:
                    dev.close()
                except Exception:
                    pass
                return self._send_json(getattr(e, "result", {"ok": False, "error": str(e)}), 500)

        self._send_json({"ok": False, "error": "Unknown endpoint"}, 404)



class WriteFailure(IOError):
    """A flash transaction stopped; report recovery data without claiming edits completed."""
    def __init__(self, error, backups, completed, failed, planned):
        super().__init__(str(error))
        self.result = {
            "ok": False, "error": str(error), "backups": backups,
            "completed_sectors": completed, "failed_sector": failed,
            "recovery_required": True, "applied_buttons": [], "changes": [],
            "planned_changes": planned,
            "notes": ["書き込みを中断しました。設定が一部だけ変更された可能性があります。"
                      "自動ロールバックは行いません。全体バックアップを保管し、SAFETY_FIXES.md の復旧手順を参照してください。"],
        }


def _plan_fingerprint(cfg, reads, result, sectors):
    digest = hashlib.sha256()
    digest.update(json.dumps(cfg, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
    for (addr, n), data in sorted(reads.items()):
        digest.update(f"{addr}:{n}:".encode())
        digest.update(data)
    digest.update(json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
    for sector, data in sorted(sectors.items()):
        digest.update(str(sector).encode())
        digest.update(data)
    return digest.hexdigest()


def _plan_token(fingerprint, expires=None):
    expires = int(time.time()) + _PLAN_TTL if expires is None else expires
    signature = hmac.new(_PLAN_KEY, f"{expires}:{fingerprint}".encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{signature}"


def _validate_plan_token(token, fingerprint):
    try:
        expires = int(token.split(".", 1)[0])
        valid = int(time.time()) <= expires <= int(time.time()) + _PLAN_TTL
        valid = valid and hmac.compare_digest(token, _plan_token(fingerprint, expires))
    except (AttributeError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("実機または編集内容が変更されたか、確認の有効期限が切れました。変更確認（ドライラン）をやり直してください。")


def _button_outcomes(sec0, new0, cfg, ready, script_notes, reader, wanted):
    eligible, skipped, warnings = [], [], []
    records = None
    for mi, mode in enumerate(cfg.get("modes", [])[:3]):
        for si, sw in enumerate(mode.get("switches", [])[:11]):
            mb = MODE_BASE + MODE_SIZE * mi
            special_changed = sw.get("role") == "special" and new0[mb + 11 + si] != sec0[mb + 11 + si]
            if not (sw.get("scriptDirty") or sw.get("releaseHold") or special_changed):
                continue
            item = {"mode": mi, "sw": si + 1, "label": button_label(mi, si)}
            if (mi, si) not in ready and sw.get("role") != "special":
                reason = next((n for n in script_notes if n.startswith(item["label"] + ":")), "未対応のため変更しません。")
                skipped.append(dict(item, reason=reason))
                continue
            changed = (new0[mb + si] != sec0[mb + si] or new0[mb + 11 + si] != sec0[mb + 11 + si]
                       or any(w["label"] == item["label"] for w in wanted))
            eligible.append(dict(item, status="planned" if changed else "already_current"))
            n = sec0[mb + si]
            if n in (0, 0xFF):
                continue
            if records is None:
                records = revomate_scripts.read_records(reader(SCRIPT_INFO, SECTOR_SIZE))
            rec = records.get(n)
            if not rec:
                continue
            old = reader(rec["addr"], rec["size"])
            if decode_button_script(old, rec.get("mode", 0)).get("role") != "macro":
                continue
            if any(w["script_no"] == n for w in wanted):
                warnings.append(f"{item['label']}: 本体のマクロ（No.{n}）を上書きします。元のマクロは全体バックアップに保存されます。")
            elif sw.get("role") in ("none", "special") and sec0[mb + 11 + si] in (0, 0xFF):
                warnings.append(f"{item['label']}: 本体のマクロ（No.{n}）の割り当てを解除・変更します（マクロ本体は残します）。")
    return eligible, skipped, warnings


def _skipped_settings(sec0, cfg):
    skipped = []
    for mi, mode in enumerate(cfg.get("modes", [])[:3]):
        mb = MODE_BASE + MODE_SIZE * mi
        for fi, df in enumerate(mode.get("dialFuncs", [])[:4]):
            a = DIAL_BASE + DIAL_MODE * mi + DIAL_FUNC * fi
            for off, side in ((0, "cw"), (8, "ccw")):
                cur = sec0[a + off:a + off + 8]
                act = df.get(side, {})
                t = int(act.get("type", 0)) & 0xFF
                label = f"モード{mi + 1} ダイヤル{fi + 1} {'右回転' if side == 'cw' else '左回転'}"
                if t not in SIMPLE_TYPES and t != cur[0]:
                    skipped.append({"mode": mi, "field": f"dialFuncs.{fi}.{side}", "label": label, "reason": "未対応のダイヤル動作のため変更しません。"})
                sens = df.get("sensCw" if side == "cw" else "sensCcw", df.get("sens"))
                if sens is not None:
                    try:
                        want = int(sens)
                    except (ValueError, TypeError):
                        want = None
                    supported = t in SIMPLE_TYPES
                    have = cur[7] if cur[0] in SIMPLE_TYPES or not supported else 0x32
                    if want != cur[7] and (want is None or not 1 <= want <= 100 or not supported or not 1 <= have <= 100):
                        skipped.append({"mode": mi, "field": f"dialFuncs.{fi}.sens{side.title()}", "label": label + " 回転スピード", "reason": "未対応の動作または範囲外の回転スピードのため変更しません。"})
            if "led" in df and led_duties(df["led"]) is None:
                skipped.append({"mode": mi, "field": f"dialFuncs.{fi}.led", "label": f"モード{mi + 1} ダイヤル{fi + 1} LED", "reason": "色の形式が不正のため変更しません。"})
        if "baseLed" in mode and led_duties(mode["baseLed"]) is None:
            skipped.append({"mode": mi, "field": "baseLed", "label": f"モード{mi + 1} 待機LED", "reason": "色の形式が不正のため変更しません。"})
    return skipped


@serialized_device_operation
def write_config(dev, cfg, dry_run=False, plan_token=None, require_plan=True):
    """Plan from device bytes; verify the confirmed plan and full backup before any erase.

    Internal initialization explicitly opts out of the page confirmation token;
    it still uses the same serialization, full backup and verified write path.
    """
    reads = {}
    def reader(addr, n):
        if not isinstance(addr, int) or not isinstance(n, int) or n < 0 or addr < 0 or addr + n > FLASH_SECTORS * SECTOR_SIZE:
            raise IOError("実機のフラッシュ範囲外は読み込みません。")
        key = (addr, n)
        if key not in reads:
            data = bytes(read_range(dev, addr, n))
            if len(data) != n or addr < 0 or addr + n > FLASH_SECTORS * SECTOR_SIZE:
                raise IOError("本体からの読み込みサイズまたはアドレスが正しくありません。")
            reads[key] = data
        return reads[key]

    sec0 = bytearray(reader(0, SECTOR_SIZE))
    wanted, script_notes, ready, assign = plan_script_rewrites(reader, sec0, cfg)
    new0, changes, notes = apply_changes(sec0, cfg, ready)
    for (mi, si), no in assign.items():
        new0[MODE_BASE + MODE_SIZE * mi + si] = no
        changes.append(f"{button_label(mi, si)}: 新しいスクリプト {no} を割り当てます")
    script_sectors, script_lines = compute_script_writes(reader, wanted) if wanted else ({}, [])
    planned_sectors = {s: bytes(data) for s, data in script_sectors.items()
                       if bytes(data) != reader(s * SECTOR_SIZE, SECTOR_SIZE)}
    if new0 != sec0:
        planned_sectors[0] = bytes(new0)
    order = sorted(s for s in planned_sectors if s > 1) + [s for s in (1, 0) if s in planned_sectors]
    eligible, skipped, warnings = _button_outcomes(sec0, new0, cfg, ready, script_notes, reader, wanted)
    skipped += _skipped_settings(sec0, cfg)
    out = {"ok": True, "backups": [], "changes": changes + script_lines,
           "notes": notes + script_notes, "warnings": warnings, "skipped": skipped,
           "eligible_buttons": eligible, "applied_buttons": [],
           "sectors": order, "has_changes": bool(order), "applied": False}
    if not order:
        out["notes"].insert(0, "変更がないため、書き込みません。未適用の編集はそのまま残します。")
    fingerprint = _plan_fingerprint(cfg, reads, out, planned_sectors)
    if dry_run:
        return dict(out, dry_run=True, plan_token=_plan_token(fingerprint))
    if require_plan:
        _validate_plan_token(plan_token, fingerprint)
    if not order:
        out["applied_buttons"] = eligible  # already matches the device, not skipped
        return out

    # No erase/write may occur until this FULL backup has been durably saved and re-read.
    backup_path = save_full_backup(dev, "before_write")
    out["backups"] = [os.path.basename(backup_path)]
    with open(backup_path, "rb") as f:
        image = f.read()
    if len(image) != FLASH_SECTORS * SECTOR_SIZE or any(image[addr:addr + n] != data for (addr, n), data in reads.items()):
        raise IOError("バックアップと確認した実機の内容が一致しません。消去・書き込みは行っていません。")
    completed, failed = [], None
    try:
        for sector in order:
            failed = sector
            data = planned_sectors[sector]
            write_sector(dev, sector, data)
            if bytes(read_range(dev, sector * SECTOR_SIZE, SECTOR_SIZE)) != data:
                raise IOError(f"セクタ {sector} の書き込み後の照合が一致しません。")
            completed.append(sector)
    except Exception as error:
        raise WriteFailure(error, out["backups"], completed, failed, out["changes"]) from error
    out["completed_sectors"] = completed
    out["applied_buttons"] = [dict(item, status="applied" if item["status"] == "planned" else "already_current") for item in eligible]
    if 0 in planned_sectors:
        try:
            push_runtime_settings(dev, bytes(new0))
            out["applied"] = True
        except Exception as e:
            out["notes"].append(f"フラッシュへの保存・照合は完了しましたが、即時反映はできませんでした（{e}）。USB を挿し直すと反映されます。")
    return out


@serialized_device_operation
def read_full_flash(dev):
    chunks = []
    for sector in range(FLASH_SECTORS):
        chunk = bytes(read_range(dev, sector * SECTOR_SIZE, SECTOR_SIZE))
        if len(chunk) != SECTOR_SIZE:
            raise IOError(f"全体バックアップの読み込みが不完全です（セクタ {sector}）。書き込みません。")
        chunks.append(chunk)
    return b"".join(chunks)


def _persist_full_backup(data, label):
    if len(data) != FLASH_SECTORS * SECTOR_SIZE:
        raise IOError("全体バックアップのサイズが正しくありません。")
    if not re.fullmatch(r"[a-z_]+", label):
        raise ValueError("bad backup label")
    fd, temporary = tempfile.mkstemp(prefix=".revomate_backup_", suffix=".tmp", dir=DATA_DIR)
    path = os.path.join(DATA_DIR, f"revomate_{label}_{time.time_ns()}_{secrets.token_hex(4)}.bin")
    try:
        with os.fdopen(fd, "wb") as f:
            if f.write(data) != len(data):
                raise IOError("全体バックアップの保存が不完全です。")
            f.flush()
            os.fsync(f.fileno())
        with open(temporary, "rb") as f:
            saved = f.read()
        if len(saved) != len(data) or saved != data:
            raise IOError("全体バックアップの保存後の照合が一致しません。書き込みません。")
        os.replace(temporary, path)
        if sys.platform != "win32":       # a folder cannot be opened like a file on Windows (the file itself was already fsynced above)
            directory_fd = os.open(DATA_DIR, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        with open(path, "rb") as f:
            if f.read() != data:
                raise IOError("確定した全体バックアップの照合が一致しません。")
        return path
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@serialized_device_operation
def save_full_backup(dev, label):
    return _persist_full_backup(read_full_flash(dev), label)





ASK_SAVE_PATH = None   # set in window mode: shows the system "save as" panel and returns the chosen path (None = cancelled)
# small settings of the app itself, in a folder named after the bundle id (so an uninstaller such as AppCleaner finds it)
if sys.platform == "win32":
    APP_SUPPORT_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "com.dialmatrix.lite")
else:
    APP_SUPPORT_DIR = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "com.dialmatrix.lite")
LAST_DIR_PATH = os.path.join(APP_SUPPORT_DIR, "last_save_dir.txt")
PING = {"last": 0.0}
IDLE_QUIT_SECONDS = 180        # packaged app: quit when the page has been closed this long
FIRST_PAGE_SECONDS = 120       # packaged app: quit when no page was ever opened


def _watch_page(server):
    started = time.time()
    while True:
        time.sleep(5)
        last = PING["last"]
        if (last and time.time() - last > IDLE_QUIT_SECONDS) or (not last and time.time() - started > FIRST_PAGE_SECONDS):
            server.shutdown()
            return




def install_save_dialog(win, webview):
    """Saving a file from the page opens the system "save as" panel (it starts in the folder used last time)."""
    global ASK_SAVE_PATH

    def ask(name):
        try:
            with open(LAST_DIR_PATH, encoding="utf-8") as f:
                start = f.read().strip()
        except OSError:
            start = ""
        if not os.path.isdir(start):
            start = os.path.join(os.path.expanduser("~"), "Documents")
        kind = "JSON (*.json)" if name.lower().endswith(".json") else "BIN (*.bin)"
        result = win.create_file_dialog(webview.SAVE_DIALOG, directory=start, save_filename=name, file_types=(kind,))
        if isinstance(result, (list, tuple)):
            result = result[0] if result else None
        if result:
            try:
                os.makedirs(APP_SUPPORT_DIR, exist_ok=True)
                with open(LAST_DIR_PATH, "w", encoding="utf-8") as f:
                    f.write(os.path.dirname(result))
            except OSError:
                pass
        return result or None

    ASK_SAVE_PATH = ask




BUNDLE_IDS = {"lite": "com.dialmatrix.lite"}
APP_NAMES = {"lite": "DialMatrix（簡易版）"}


def other_instance_running():
    """Another Rev-O-mate app that is already running (this one, started twice, or the other edition): (edition, app) or None."""
    try:
        import AppKit
        me = os.getpid()
        for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
            if app.processIdentifier() == me:
                continue
            for ed, bid in BUNDLE_IDS.items():
                if app.bundleIdentifier() == bid:
                    return ed, app
    except Exception:
        pass
    return None


def refuse_to_start(found):
    """Tell the person why this app does not start (a second one would fight over the device), then the caller exits."""
    ed, running = found
    mine = "lite"
    if ed == mine:
        title = f"{APP_NAMES[mine]} は、すでに起動しています"
        body = "すでに開いているウィンドウを、使ってください。"
    if os.environ.get("REVOMATE_NO_DIALOG"):
        print(f"[起動しません] {title} / {body}")
        return
    try:
        import AppKit
        AppKit.NSApplication.sharedApplication()
        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(body.replace("\\n", "\n"))
        alert.addButtonWithTitle_("OK")
        alert.runModal()
        if ed == mine and running is not None:
            running.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
    except Exception:
        print(f"{title}: {body}")


def port_answers(port):
    """True when some program already listens on this local port."""
    import socket
    with socket.socket() as c:
        c.settimeout(0.5)
        return c.connect_ex(("127.0.0.1", port)) == 0


def main():
    if getattr(sys, "frozen", False) or os.environ.get("REVOMATE_CHECK_SINGLE"):
        found = other_instance_running()
        if found:
            refuse_to_start(found)
            return
    global PORT
    try:
        if getattr(sys, "frozen", False) and "REVOMATE_PORT" not in os.environ and port_answers(PORT):
            raise OSError("the port is answered by another program")      # binding alone can succeed next to a program that listens on all addresses
        server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), StudioHandler)
    except OSError:
        server = None
        if getattr(sys, "frozen", False) and "REVOMATE_PORT" not in os.environ:
            # the port is taken by another program (the single-instance check above already ruled out this app itself):
            # let the system pick a free one, so the window never shows the other program's page
            try:
                server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), StudioHandler)
                PORT = server.server_address[1]
            except OSError:
                server = None
    url = f"http://127.0.0.1:{PORT}"
    if server is None:
        # already running (e.g. the .app was opened twice): just show the page again
        print(f"DialMatrix is already running at {url}")
        if "--no-browser" not in sys.argv:
            webbrowser.open(url)
        return
    print(f"DialMatrix running at {url}")
    print("Press Ctrl-C to stop.")
    use_window = (getattr(sys, "frozen", False) or "--window" in sys.argv) and "--no-browser" not in sys.argv and "--browser" not in sys.argv
    if use_window:
        try:
            import webview
        except ImportError:
            use_window = False
    if use_window:
        # Own window with only the title bar.
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        webview.settings["ALLOW_DOWNLOADS"] = True
        title = "DialMatrix"
        win = webview.create_window(title, url, width=1500, height=940, min_size=(1000, 640))
        quitting = threading.Event()
        install_save_dialog(win, webview)

        def quit_all():
            quitting.set()
            server.shutdown()
            os._exit(0)

        after_start = None       # closing the window quits the app

        threading.Thread(target=lambda: (thread.join(), quitting.set(), win.destroy()), daemon=True).start()   # the page's 終了 button
        try:
            webview.start(after_start)
        finally:
            server.shutdown()
        return
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    if getattr(sys, "frozen", False):
        threading.Thread(target=_watch_page, args=(server,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
