"""Entry point .exe untuk Windows 7 / 8 / 8.1.

Sejak v0.3 UI-nya native (Tkinter), jadi build ini sama seperti build utama:
satu jendela aplikasi, tanpa WebView2 dan tanpa browser. Tk 8.6 sudah tersedia
di Python 3.8+ untuk Windows 7/8/8.1.
"""
from __future__ import annotations

import multiprocessing
import os
import sys


def main() -> int:
    multiprocessing.freeze_support()

    try:
        exe_dir = os.path.dirname(os.path.abspath(
            sys.executable if getattr(sys, "frozen", False) else __file__))
        if getattr(sys, "frozen", False) and os.path.exists(os.path.join(exe_dir, "portable.txt")):
            os.environ.setdefault("STINTLAB_DATA", os.path.join(exe_dir, "data"))
    except Exception:
        pass

    from app.main import main as real_main

    argv = sys.argv[1:]
    if not any(a.startswith("--") for a in argv):
        argv = ["--native"] + argv
    return real_main(argv)


if __name__ == "__main__":
    sys.exit(main())
