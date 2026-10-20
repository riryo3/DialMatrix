"""Rev-O-mate (Bit Trade One, VID 0x22EA / PID 0x004B) userspace HID driver.

The device exposes several HID interfaces (keyboard + consumer control,
mouse, gamepad, vendor-defined). This driver opens each one, decodes the
reports it understands and delivers events to callbacks.

    from revomate import RevOMate
    dev = RevOMate()
    dev.on_event(lambda ev: print(ev))
    dev.run()

Run `python3 revomate.py` to print events. macOS needs Input Monitoring
permission for the terminal app that runs this.
"""
import ctypes
import threading

VID = 0x22EA
PID = 0x004B

MODIFIERS = ["lctrl", "lshift", "lalt", "lgui", "rctrl", "rshift", "ralt", "rgui"]

# HID keyboard usage ID -> name (USB HID Usage Tables, keyboard page 0x07)
KEY_NAMES = {
    **{0x04 + i: chr(ord("a") + i) for i in range(26)},
    **{0x1E + i: str((i + 1) % 10) for i in range(10)},
    0x28: "enter", 0x29: "esc", 0x2A: "backspace", 0x2B: "tab", 0x2C: "space",
    0x2D: "-", 0x2E: "=", 0x2F: "[", 0x30: "]", 0x31: "\\", 0x33: ";", 0x34: "'",
    0x35: "`", 0x36: ",", 0x37: ".", 0x38: "/", 0x39: "capslock",
    **{0x3A + i: f"f{i + 1}" for i in range(12)},
    0x46: "printscreen", 0x47: "scrolllock", 0x48: "pause", 0x49: "insert",
    0x4A: "home", 0x4B: "pageup", 0x4C: "delete", 0x4D: "end", 0x4E: "pagedown",
    0x4F: "right", 0x50: "left", 0x51: "down", 0x52: "up",
    **{0x68 + i: f"f{13 + i}" for i in range(12)},
}

# Consumer control usages, in the bit order of the report descriptor (report ID 2).
CONSUMER_USAGES = [
    "play", "pause", "record", "fast_forward", "rewind", "next_track",
    "prev_track", "stop", "play_pause", "mute", "volume_up", "volume_down",
]


class Event(dict):
    """dict with attribute access: kind, action ('press'/'release'/'move'), name, raw."""

    __getattr__ = dict.__getitem__


def _diff(prev, cur):
    return sorted(cur - prev), sorted(prev - cur)


class RevOMate:
    def __init__(self, vid=VID, pid=PID):
        self.vid, self.pid = vid, pid
        self._callbacks = []
        self._stop = threading.Event()
        self._keys = set()
        self._consumer = set()

    def on_event(self, fn):
        self._callbacks.append(fn)
        return fn

    def _emit(self, **kw):
        ev = Event(kw)
        for fn in self._callbacks:
            fn(ev)

    # --- report decoders -------------------------------------------------
    def _decode_keyboard(self, data):
        # [report_id=1, modifiers, reserved, key1..key6]
        mods = {MODIFIERS[i] for i in range(8) if data[1] >> i & 1}
        keys = {KEY_NAMES.get(k, f"key_{k:#04x}") for k in data[3:9] if k > 1}
        cur = mods | keys
        pressed, released = _diff(self._keys, cur)
        self._keys = cur
        for n in pressed:
            self._emit(kind="keyboard", action="press", name=n, raw=bytes(data))
        for n in released:
            self._emit(kind="keyboard", action="release", name=n, raw=bytes(data))

    def _decode_consumer(self, data):
        # [report_id=2, bitmask lo, bitmask hi]
        bits = data[1] | (data[2] << 8 if len(data) > 2 else 0)
        cur = {CONSUMER_USAGES[i] for i in range(len(CONSUMER_USAGES)) if bits >> i & 1}
        pressed, released = _diff(self._consumer, cur)
        self._consumer = cur
        for n in pressed:
            self._emit(kind="consumer", action="press", name=n, raw=bytes(data))
        for n in released:
            self._emit(kind="consumer", action="release", name=n, raw=bytes(data))

    def _handle(self, info, data):
        # keyboard (report ID 1) and consumer control (report ID 2) share one interface
        kbd_if = (info["usage_page"], info["usage"]) in ((1, 6), (12, 1))
        if kbd_if and data[0] == 1 and len(data) >= 9:
            self._decode_keyboard(data)
        elif kbd_if and data[0] == 2:
            self._decode_consumer(data)
        else:
            # mouse / gamepad / vendor reports: layout not known yet, pass through raw
            self._emit(kind=f"hid_{info['usage_page']:#x}_{info['usage']:#x}",
                       action="report", name=data.hex(), raw=bytes(data))

    # --- plumbing (IOHIDManager: non-exclusive, so system keyboards can be read) --
    def run(self):
        """Listen to all interfaces and block until stop() or Ctrl-C."""
        mgr = _iohid.create_manager(self.vid, self.pid)
        usages = {}

        @_iohid.REPORT_CB
        def on_report(ctx, result, sender, rtype, rid, buf, length):
            if sender not in usages:
                usages[sender] = _iohid.usage_of(sender)
            data = bytes(buf[:length])
            if rid and (not data or data[0] != rid):
                data = bytes([rid]) + data
            page, usage = usages[sender]
            self._handle({"usage_page": page, "usage": usage}, data)

        _iohid.start(mgr, on_report)
        try:
            while not self._stop.is_set():
                _iohid.pump(0.2)
        except KeyboardInterrupt:
            pass
        finally:
            _iohid.close(mgr)

    def stop(self):
        self._stop.set()


class _iohid:
    """Thin ctypes wrapper around the parts of IOHIDManager we need."""

    _io = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
    _cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    REPORT_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p,
                                 ctypes.c_uint32, ctypes.c_uint32,
                                 ctypes.POINTER(ctypes.c_uint8), ctypes.c_long)
    _P = ctypes.c_void_p
    _mode = _P.in_dll(_cf, "kCFRunLoopDefaultMode")

    _cf.CFStringCreateWithCString.restype = _P
    _cf.CFStringCreateWithCString.argtypes = [_P, ctypes.c_char_p, ctypes.c_uint32]
    _cf.CFNumberCreate.restype = _P
    _cf.CFNumberCreate.argtypes = [_P, ctypes.c_int, ctypes.c_void_p]
    _cf.CFNumberGetValue.argtypes = [_P, ctypes.c_int, ctypes.c_void_p]
    _cf.CFDictionaryCreate.restype = _P
    _cf.CFDictionaryCreate.argtypes = [_P, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, _P, _P]
    _cf.CFRunLoopGetCurrent.restype = _P
    _cf.CFRunLoopRunInMode.argtypes = [_P, ctypes.c_double, ctypes.c_bool]
    _io.IOHIDManagerCreate.restype = _P
    _io.IOHIDManagerCreate.argtypes = [_P, ctypes.c_uint32]
    _io.IOHIDManagerSetDeviceMatching.argtypes = [_P, _P]
    _io.IOHIDManagerRegisterInputReportCallback.argtypes = [_P, REPORT_CB, _P]
    _io.IOHIDManagerScheduleWithRunLoop.argtypes = [_P, _P, _P]
    _io.IOHIDManagerOpen.restype = ctypes.c_int32
    _io.IOHIDManagerOpen.argtypes = [_P, ctypes.c_uint32]
    _io.IOHIDManagerClose.argtypes = [_P, ctypes.c_uint32]
    _io.IOHIDDeviceGetProperty.restype = _P
    _io.IOHIDDeviceGetProperty.argtypes = [_P, _P]

    @classmethod
    def _str(cls, text):
        return cls._cf.CFStringCreateWithCString(None, text.encode(), 0x08000100)

    @classmethod
    def _num(cls, n):
        v = ctypes.c_int32(n)
        return cls._cf.CFNumberCreate(None, 3, ctypes.byref(v))

    @classmethod
    def create_manager(cls, vid, pid):
        mgr = cls._io.IOHIDManagerCreate(None, 0)
        keys = (cls._P * 2)(cls._str("VendorID"), cls._str("ProductID"))
        vals = (cls._P * 2)(cls._num(vid), cls._num(pid))
        d = cls._cf.CFDictionaryCreate(
            None, keys, vals, 2,
            ctypes.addressof(cls._P.in_dll(cls._cf, "kCFTypeDictionaryKeyCallBacks")),
            ctypes.addressof(cls._P.in_dll(cls._cf, "kCFTypeDictionaryValueCallBacks")))
        cls._io.IOHIDManagerSetDeviceMatching(mgr, d)
        return mgr

    @classmethod
    def start(cls, mgr, cb):
        cls._io.IOHIDManagerRegisterInputReportCallback(mgr, cb, None)
        cls._io.IOHIDManagerScheduleWithRunLoop(mgr, cls._cf.CFRunLoopGetCurrent(), cls._mode)
        r = cls._io.IOHIDManagerOpen(mgr, 0)  # 0 = not exclusive
        if r != 0:
            hint = " (needs Input Monitoring permission)" if r & 0xFFFFFFFF == 0xE00002E2 else ""
            raise RuntimeError(f"IOHIDManagerOpen failed: {r & 0xFFFFFFFF:#x}{hint}")

    @classmethod
    def usage_of(cls, device):
        out = []
        for key in ("PrimaryUsagePage", "PrimaryUsage"):
            ref = cls._io.IOHIDDeviceGetProperty(device, cls._str(key))
            v = ctypes.c_int32(0)
            if ref:
                cls._cf.CFNumberGetValue(ref, 3, ctypes.byref(v))
            out.append(v.value)
        return tuple(out)

    @classmethod
    def pump(cls, seconds):
        cls._cf.CFRunLoopRunInMode(cls._mode, seconds, False)

    @classmethod
    def close(cls, mgr):
        cls._io.IOHIDManagerClose(mgr, 0)


if __name__ == "__main__":
    dev = RevOMate()
    dev.on_event(lambda e: print(e["kind"], e["action"], e["name"], flush=True))
    print("listening... (Ctrl-C to quit)")
    dev.run()
