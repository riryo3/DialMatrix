"""Show a colour on the device's LED for a moment (the settings screen uses it while a colour is being chosen).

Uses the LED "preview" command (0x64): nothing is written to the flash, and the device goes back to its own colour by itself
when the time is up. The device is opened only while it is used and let go of a few seconds later, so that other tools
(and this app's own reading / writing, which calls `pause()`) are not in the way.
"""
import re
import threading
import time

VID, PID = 0x22EA, 0x004B
LED_PREVIEW_CMD, GET_MODE_CMD, CMD_READ = 0x64, 0x31, 0x11
LED_GAMMA, LED_MAX_DUTY = 2.0, 90
OPEN_RETRY = 3.0           # seconds to wait before trying to open the device again after a failure
RELEASE_AFTER = 4.0        # let go of the device this long after the last use

_lock = threading.RLock()
_dev = None
_last_use = 0.0
_last_fail = 0.0
_paused_until = 0.0
_base_level = 0
_timer = None


def hex_rgb(text, default=(255, 255, 255)):
    m = re.fullmatch(r"#?([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})", str(text).strip())
    return tuple(int(x, 16) for x in m.groups()) if m else default


def duties(rgb, level=1.0):
    """Screen colour (0..255) times a brightness 0..1 -> the device's duty (0..90); same conversion as the settings page."""
    return [max(0, min(LED_MAX_DUTY, round(((v / 255) ** LED_GAMMA) * level * LED_MAX_DUTY))) for v in rgb]


def pause(seconds=120.0):
    """Stop using the device for a while (the app is about to read / write it itself)."""
    global _paused_until
    _paused_until = time.time() + seconds
    close()


def resume():
    global _paused_until
    _paused_until = 0.0


def _xfer(cmd, payload=()):
    buf = [0, cmd, *payload] + [0xFF] * 60
    _dev.write(buf[:65])
    for _ in range(5):
        r = _dev.read(64, 400)
        if r and r[0] == cmd:
            return r
    raise OSError("no answer")


def _read_level():
    """The brightness level of the mode that is on now (the preview command wants it)."""
    try:
        mode = _xfer(GET_MODE_CMD)[1]
        mode = mode if mode <= 2 else 0
        addr = 0x10 + 0x20 * mode + 0x17
        r = _xfer(CMD_READ, (addr >> 24 & 0xFF, addr >> 16 & 0xFF, addr >> 8 & 0xFF, addr & 0xFF, 8))
        lv = r[2:10][5]
        return lv if lv < 3 else 0
    except Exception:
        return 0


def _open():
    global _dev, _last_fail, _base_level
    if _dev is not None:
        return True
    if time.time() < _paused_until or time.time() - _last_fail < OPEN_RETRY:
        return False
    try:
        import hid
        for info in hid.enumerate(VID, PID):
            if info.get("usage_page") == 0xFF00:
                d = hid.device()
                d.open_path(info["path"])
                _dev = d
                break
        if _dev is None:
            raise OSError("device not found")
        _base_level = _read_level()
        return True
    except Exception:
        _dev = None
        _last_fail = time.time()
        return False


def close():
    global _dev
    with _lock:
        if _dev is not None:
            try:
                _dev.close()
            except Exception:
                pass
            _dev = None


def _release_if_idle():
    if time.time() - _last_use >= RELEASE_AFTER:
        close()


def show(duty, hold_ms):
    """Light the LED with `duty` (R, G, B in 0..90) for `hold_ms`; the device then goes back to its own colour."""
    global _last_use, _timer
    with _lock:
        if time.time() < _paused_until or not _open():
            return False
        try:
            ticks = max(1, min(0xFFFF, int(hold_ms * 10)))          # the device counts in 100 microseconds
            r, g, b = (list(duty) + [0, 0, 0])[:3]
            buf = [0, LED_PREVIEW_CMD, 3, ticks & 0xFF, ticks >> 8, _base_level, r, g, b] + [0xFF] * 56
            _dev.write(buf[:65])
            _last_use = time.time()
            if _timer is not None:
                _timer.cancel()
            _timer = threading.Timer(RELEASE_AFTER + 0.2, _release_if_idle)
            _timer.daemon = True
            _timer.start()
            return True
        except Exception:
            close()
            return False


def preview(color_hex, ms):
    """Light the LED once with a colour chosen on the settings screen (so it can be judged on the device)."""
    resume()                                   # the person is looking at the settings now: the LED may be used
    return show(duties(hex_rgb(color_hex), 1.0), max(60, min(3000, int(ms))))
