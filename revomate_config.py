"""Show / edit Rev-O-mate dial settings from macOS.

Layout (from the vendor's open-source app, FlashControl.cs), all in flash sector 0:
    0x000        base info (byte 0 = current mode)
    0x010 + 0x20*mode     per-mode switch info (11 script numbers, 11 special funcs,
                          byte 0x16 = default dial function)
    0x100 + 0x60*mode + 0x18*func   dial function: CW[8] CCW[8] LED[8]
    0xB00 + 0x58*mode + 8*sw        switch function data (8 bytes each)

An 8-byte action is [type, modifier, keycode, ..., sensitivity at byte 7]:
    type 9 = keyboard key, 10 play, 11 pause, 12 stop, 13 rec, 14 fast-forward,
    15 rewind, 16 next, 17 previous, 18 mute, 19 volume up, 20 volume down.

    python3 revomate_config.py show
    python3 revomate_config.py dial-media MODE FUNC CW CCW   # e.g. 0 0 volume_up volume_down
    python3 revomate_config.py button-media MODE SW NAME     # SW 1-10 buttons, 11 = dial push
                                                             # NAME: play, pause, next, previous, volume_up, ...
    python3 revomate_config.py restore-sector SECTOR FILE    # undo a write from its backup
"""
import sys
import time

from revomate_reset import CMD_ERASE, CMD_READ, CMD_WRITE, READ_CHUNK, SECTOR_SIZE, WRITE_CHUNK, open_vendor, xfer

MEDIA = {"play": 10, "pause": 11, "stop": 12, "rec": 13, "fast_forward": 14,
         "rewind": 15, "next": 16, "previous": 17, "mute": 18,
         "volume_up": 19, "volume_down": 20}
# Inside scripts the media operand is the dial/switch type code minus 9
# (measured on the device: 10 -> volume up, 8 -> previous track).
SCRIPT_MEDIA = {name: code - 9 for name, code in MEDIA.items()}
TYPE_NAMES ={0: "none", 9: "key", **{v: k for k, v in MEDIA.items()}}

DIAL_BASE, DIAL_MODE, DIAL_FUNC = 0x100, 0x60, 0x18
SW_BASE, SW_MODE = 0xB00, 0x58
MODE_BASE, MODE_SIZE = 0x10, 0x20
N_MODES, N_FUNCS = 4, 4
SCRIPT_INFO = 0x10000


def read_range(d, start, size):
    out = bytearray()
    while len(out) < size:
        n = min(READ_CHUNK, size - len(out))
        r = xfer(d, CMD_READ, start + len(out), n)
        if r[1] != n:
            raise IOError("short read")
        out += bytes(r[2:2 + n])
    return bytes(out)


def write_sector(d, sector, data):
    """Erase one 64 KB sector and write `data` (len == SECTOR_SIZE) back."""
    assert len(data) == SECTOR_SIZE
    base = sector * SECTOR_SIZE
    r = xfer(d, CMD_ERASE, base)
    if r[1] != 0:
        raise IOError("erase failed")
    off = 0
    while off < SECTOR_SIZE:
        n = min(WRITE_CHUNK, 0x100 - (off & 0xFF))
        chunk = data[off:off + n]
        if any(b != 0xFF for b in chunk):
            a = base + off
            buf = [0, CMD_WRITE, a >> 24 & 0xFF, a >> 16 & 0xFF, a >> 8 & 0xFF, a & 0xFF, n] + list(chunk)
            buf += [0xFF] * (65 - len(buf))
            d.write(buf)
            rr = d.read(64, 1000)
            if not rr or rr[0] != CMD_WRITE or rr[1] != 0:
                raise IOError(f"write failed at {a:#x}")
        off += n
    if read_range(d, base, SECTOR_SIZE) != data:
        raise IOError("verify mismatch")


def describe(act):
    t = act[0]
    if t == 9:
        return f"key(mod={act[1]:#04x}, code={act[2]:#04x})"
    return TYPE_NAMES.get(t, f"type{t}") + f" [{' '.join(f'{b:02x}' for b in act)}]"


def show(sec):
    print(f"current mode byte: {sec[0]}")
    for m in range(N_MODES):
        mb = MODE_BASE + MODE_SIZE * m
        print(f"mode {m}: scripts SW1-11 = {[sec[mb + i] for i in range(11)]}, "
              f"special = {[sec[mb + 11 + i] for i in range(11)]}, default dial func = {sec[mb + 0x16]}")
        for f in range(N_FUNCS):
            a = DIAL_BASE + DIAL_MODE * m + DIAL_FUNC * f
            print(f"   dial func {f}: CW {describe(sec[a:a + 8])}  |  CCW {describe(sec[a + 8:a + 16])}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    dev = open_vendor()
    sector0 = read_range(dev, 0, SECTOR_SIZE)
    if cmd == "show":
        show(sector0)
    elif cmd == "dial-media" and len(sys.argv) == 6:
        mode, func, cw, ccw = int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5]
        new = bytearray(sector0)
        a = DIAL_BASE + DIAL_MODE * mode + DIAL_FUNC * func
        for off, name in ((0, cw), (8, ccw)):
            old = new[a + off:a + off + 8]
            new[a + off:a + off + 8] = bytes([MEDIA[name], 0, 0, 0, 0, 0, 0, old[7]])  # keep sensitivity
        path = f"revomate_sector0_backup_{int(time.time())}.bin"
        open(path, "wb").write(sector0)
        print(f"backup of sector 0 saved to {path}")
        write_sector(dev, 0, bytes(new))
        print("written and verified. Unplug and replug the device.")
        show(read_range(dev, 0, SECTOR_SIZE))
    elif cmd == "button-media" and len(sys.argv) == 5:
        # Rewrite the script run by a switch so it presses/releases one media key.
        # Script bytes (from the vendor app): 43 t = media press, 42 t = media release,
        # 70 hh ll = wait. The script keeps its original length (padded with repeated releases).
        mode, sw, name = int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
        t = SCRIPT_MEDIA[name]
        mb = MODE_BASE + MODE_SIZE * mode
        script_no = sector0[mb + sw - 1]
        if script_no == 0:
            sys.exit("that switch runs no script")
        info = read_range(dev, SCRIPT_INFO + 0x10 + 0x110 * (script_no - 1), 12)
        addr = int.from_bytes(info[0:4], "little")
        size = int.from_bytes(info[4:8], "little")
        if size < 7:
            sys.exit(f"script {script_no} is too short ({size} bytes)")
        body = [0x43, t, 0x70, 0x00, 0x01, 0x42, t]
        rest = size - len(body)
        if rest % 2:
            body += [0x70, 0x00, 0x01]
            rest -= 3
        if rest < 0:
            sys.exit(f"script {script_no} is too short ({size} bytes)")
        body += [0x42, t] * (rest // 2)
        assert len(body) == size
        sec, off = addr // SECTOR_SIZE, addr % SECTOR_SIZE
        data = read_range(dev, sec * SECTOR_SIZE, SECTOR_SIZE)
        print(f"script {script_no} at {addr:#x} ({size} bytes): {data[off:off + size].hex(' ')}")
        print(f"        -> {bytes(body).hex(' ')}")
        path = f"revomate_sector{sec:#x}_backup_{int(time.time())}.bin"
        open(path, "wb").write(data)
        print(f"backup of sector {sec:#x} saved to {path}")
        new = bytearray(data)
        new[off:off + size] = bytes(body)
        write_sector(dev, sec, bytes(new))
        print("written and verified. Unplug and replug the device.")
    elif cmd == "restore-sector0" and len(sys.argv) == 3:
        write_sector(dev, 0, open(sys.argv[2], "rb").read())
        print("sector 0 restored and verified. Unplug and replug the device.")
    elif cmd == "restore-sector" and len(sys.argv) == 4:
        write_sector(dev, int(sys.argv[2], 0), open(sys.argv[3], "rb").read())
        print("sector restored and verified. Unplug and replug the device.")
    else:
        sys.exit(__doc__)
