"""THIRD_PARTY_NOTICES.md を作る（アプリに同梱して、画面の「ライセンス表示」で見せる）。

公式（株式会社ビット・トレード・ワン）の許諾文と、アプリに入る部品（pywebview・hidapi・PyObjC・bottle など）の許諾文を、
build_app/.venv の中の配布情報から集める。 使い方:  build_app/.venv/bin/python build_app/make_notices.py
"""
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SITE = glob.glob(os.path.join(HERE, ".venv", "lib", "python3.*", "site-packages"))[0]

# (shown name, dist-info folder prefix, the licence file to quote (None = the first one found), a note)
PARTS = [
    ("pywebview", "pywebview-", "LICENSE", "BSD 3-Clause. The window of the app."),
    ("hidapi (cython-hidapi)", "hidapi-", "LICENSE-bsd.txt", "Used under its BSD licence. Talks to the device."),
    ("PyObjC (pyobjc-core and frameworks)", "pyobjc_framework_webkit-", "LICENSE.txt", "MIT. Python's way to the Mac's own parts."),
    ("bottle", "bottle-", "LICENSE", "MIT. Inside pywebview."),
    ("typing_extensions", "typing_extensions-", "LICENSE", "PSF-2.0."),
]


def licence_text(prefix, name):
    for d in glob.glob(os.path.join(SITE, prefix + "*.dist-info")):
        for p in (os.path.join(d, "licenses", name), os.path.join(d, name)):
            if os.path.exists(p):
                return open(p, encoding="utf-8", errors="replace").read().strip()
    return "(licence text not found in the installed package: see the project page)"


def main():
    out = ["# Third-party notices / ライセンス表示", "",
           "DialMatrix (an unofficial tool for Bit Trade One's Rev-O-mate) uses the work of others. Their notices are below.", "",
           "## Bit Trade One — Rev-O-mate (BFROM11BK) open source hardware and software", "",
           "The way these apps talk to the device (commands, how the settings are stored in the flash) was learned from the public",
           "source of Bit Trade One's own app: https://github.com/bit-trade-one/BFROM11BK_Rev-O-mate",
           "The \"factory settings\" restore uses the factory preset file published there (it is not part of these apps; it is read from the",
           "backup folder when you put it there).", "",
           "```", open(os.path.join(ROOT, "licenses", "Assembly_Desk_License_BitTradeOne.md"), encoding="utf-8").read().strip(), "```", ""]
    for name, prefix, lic, note in PARTS:
        out += [f"## {name}", "", note, "", "```", licence_text(prefix, lic), "```", ""]
    out += ["## Python and PyInstaller", "",
            "The apps are packed with PyInstaller (GPL-2.0 with the exception that allows the packed program to be distributed under its own licence) ",
            "and contain the Python runtime (PSF License): https://docs.python.org/3/license.html", ""]
    path = os.path.join(ROOT, "THIRD_PARTY_NOTICES.md")
    open(path, "w", encoding="utf-8").write("\n".join(out))
    print(path, os.path.getsize(path), "bytes")


if __name__ == "__main__":
    main()
