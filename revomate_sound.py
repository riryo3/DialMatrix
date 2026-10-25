"""Completion sound: the person's choice (on / off, which macOS system sound for success and failure) and playing it.
Shared code of both editions, but each edition keeps its own choice (they are separate apps):
~/Library/Application Support/DialMatrix/done_sound_lite.json and done_sound_pro.json."""
import json
import os
import subprocess
import sys

WIN = sys.platform == "win32"
SOUND_DIR = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Media") if WIN else "/System/Library/Sounds"
SOUND_EXT = ".wav" if WIN else ".aiff"
DEFAULTS = {"enabled": True, "asked": False, "ok": "Windows Notify System Generic", "fail": "Windows Critical Stop"} if WIN \
    else {"enabled": True, "asked": False, "ok": "Glass", "fail": "Basso"}


def prefs_path(edition="lite"):
    edition = "pro" if edition == "pro" else "lite"
    base = os.environ.get("REVOMATE_SOUND_PREFS")                # tests: a folder of their own, never the person's real files
    if base:
        return os.path.join(base, f"done_sound_{edition}.json")
    if WIN:
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "DialMatrix", f"done_sound_{edition}.json")
    return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "DialMatrix", f"done_sound_{edition}.json")


def sounds():
    try:
        return sorted(os.path.splitext(n)[0] for n in os.listdir(SOUND_DIR) if n.endswith(SOUND_EXT))
    except OSError:
        return []


def clean(raw):
    """Only known keys, only booleans for the switches, only names of sounds that exist."""
    raw = raw if isinstance(raw, dict) else {}
    names = sounds()
    out = dict(DEFAULTS)
    for k in ("enabled", "asked"):
        if isinstance(raw.get(k), bool):
            out[k] = raw[k]
    for k in ("ok", "fail"):
        if isinstance(raw.get(k), str) and raw[k] in names:
            out[k] = raw[k]
    return out


def load(edition="lite"):
    try:
        with open(prefs_path(edition), encoding="utf-8") as f:
            return clean(json.load(f))
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save(raw, edition="lite"):
    prefs = clean(raw)
    path = prefs_path(edition)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(prefs, f, ensure_ascii=False)
    os.replace(tmp, path)
    return prefs


def play(name):
    """Start the sound and return at once. Only a sound that is in the system folder can be played."""
    if name not in sounds():
        return False
    try:
        if WIN:
            import winsound
            winsound.PlaySound(os.path.join(SOUND_DIR, name + SOUND_EXT), winsound.SND_FILENAME | winsound.SND_ASYNC)
            return True
        subprocess.Popen(["afplay", os.path.join(SOUND_DIR, name + SOUND_EXT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False
