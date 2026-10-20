"""Backup / factory-reset tool for Rev-O-mate (Bit Trade One BFROM11).

Protocol taken from the vendor's open-source config app
(github.com/bit-trade-one/BFROM11BK_Rev-O-mate, App_source/.../Form1.cs):
64-byte reports on the vendor-defined HID interface (usage page 0xFF00).

    0x11 addr[4] size   -> read flash:   reply [0x11, size, data...]
    0x12 addr[4] size data -> write flash (within a 0x100 page): reply [0x12, status]
    0x13 addr[4]        -> erase sector: reply [0x13, status]  (0 = ok)

Flash is 0x200000 bytes in 32 sectors of 0x10000. The vendor app's
"factory reset" erases all sectors; the device then re-creates defaults,
so unplug and replug it afterwards.

    python3 revomate_reset.py backup [file]   # read-only
    python3 revomate_reset.py reset           # backs up first, asks, erases
    python3 revomate_reset.py reset --no-backup --yes   # erase without backup or prompt
"""
import sys
import time

import hid

VID, PID = 0x22EA, 0x004B
FLASH_SIZE = 0x200000
SECTOR_SIZE = 0x10000
READ_CHUNK = 62
CMD_READ, CMD_WRITE, CMD_ERASE = 0x11, 0x12, 0x13
WRITE_CHUNK = 58


def open_vendor():
    for info in hid.enumerate(VID, PID):
        if info["usage_page"] == 0xFF00:
            d = hid.device()
            d.open_path(info["path"])
            return d
    sys.exit("Rev-O-mate (vendor interface) not found")


def xfer(d, cmd, addr, size=None):
    buf = [0, cmd, addr >> 24 & 0xFF, addr >> 16 & 0xFF, addr >> 8 & 0xFF, addr & 0xFF]
    if size is not None:
        buf.append(size)
    buf += [0xFF] * (65 - len(buf))
    d.write(buf)
    for _ in range(5):
        r = d.read(64, 1000)
        if r and r[0] == cmd:
            return r
    raise IOError(f"no reply to command {cmd:#x} at {addr:#x}")


def backup(d, path):
    data = bytearray()
    addr = 0
    while addr < FLASH_SIZE:
        n = min(READ_CHUNK, FLASH_SIZE - addr)
        r = xfer(d, CMD_READ, addr, n)
        if r[1] != n:
            raise IOError(f"short read at {addr:#x}")
        data += bytes(r[2:2 + n])
        addr += n
        if addr % 0x10000 < READ_CHUNK:
            print(f"\rbackup {addr * 100 // FLASH_SIZE}%", end="", flush=True)
    print()
    with open(path, "wb") as f:
        f.write(data)
    print(f"saved {len(data)} bytes to {path}")


def restore(d, path):
    """Write a 2 MB flash image (e.g. the vendor's factory_preset.mbf) to an erased device."""
    img = open(path, "rb").read()
    if len(img) != FLASH_SIZE:
        sys.exit(f"{path}: expected {FLASH_SIZE} bytes, got {len(img)}")
    addr = 0
    while addr < FLASH_SIZE:
        n = min(WRITE_CHUNK, 0x100 - (addr & 0xFF))
        chunk = img[addr:addr + n]
        if any(b != 0xFF for b in chunk):  # device was erased, so skip blank chunks
            buf = [0, CMD_WRITE, addr >> 24 & 0xFF, addr >> 16 & 0xFF, addr >> 8 & 0xFF,
                   addr & 0xFF, n] + list(chunk)
            buf += [0xFF] * (65 - len(buf))
            d.write(buf)
            r = d.read(64, 1000)
            if not r or r[0] != CMD_WRITE or r[1] != 0:
                raise IOError(f"write failed at {addr:#x}: {r}")
        addr += n
        if addr % 0x10000 < n:
            print(f"\rwrite {addr * 100 // FLASH_SIZE}%", end="", flush=True)
    print()
    for a in range(0, FLASH_SIZE, READ_CHUNK):
        n = min(READ_CHUNK, FLASH_SIZE - a)
        if bytes(xfer(d, CMD_READ, a, n)[2:2 + n]) != img[a:a + n]:
            raise IOError(f"verify mismatch at {a:#x}")
    print("verified OK. Unplug and replug the device.")


def reset(d):
    for s in range(FLASH_SIZE // SECTOR_SIZE):
        r = xfer(d, CMD_ERASE, s * SECTOR_SIZE)
        if r[1] != 0:
            raise IOError(f"erase failed at sector {s}, status {r[1]}")
        print(f"\rerase {s + 1}/{FLASH_SIZE // SECTOR_SIZE}", end="", flush=True)
    print("\ndone. Unplug and replug the Rev-O-mate (do NOT unplug before this).")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    dev = open_vendor()
    if cmd == "backup":
        backup(dev, sys.argv[2] if len(sys.argv) > 2 else f"revomate_backup_{int(time.time())}.bin")
    elif cmd == "reset":
        skip = "--no-backup" in sys.argv
        if not skip:
            backup(dev, f"revomate_backup_{int(time.time())}.bin")
        if "--yes" in sys.argv or input("Erase ALL settings on the device? type 'yes': ") == "yes":
            reset(dev)
        else:
            print("aborted")
    elif cmd == "restore" and len(sys.argv) > 2:
        restore(dev, sys.argv[2])
    else:
        sys.exit(__doc__)
