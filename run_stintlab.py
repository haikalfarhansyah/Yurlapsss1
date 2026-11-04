"""Entry point PyInstaller (YurLaps).

Dijalankan oleh .exe hasil build: default langsung mode desktop (jendela
aplikasi). Argumen CLI tetap bisa dipakai, mis. ``YurLaps.exe --no-browser``.
"""
from __future__ import annotations

import multiprocessing
import os
import sys


def main() -> int:
    multiprocessing.freeze_support()

    # Pastikan folder data bisa ditulis (portable: letakkan di sebelah .exe
    # dengan membuat file "portable.txt").
    try:
        exe_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
        if getattr(sys, "frozen", False) and os.path.exists(os.path.join(exe_dir, "portable.txt")):
            os.environ.setdefault("YURLAPS_DATA", os.path.join(exe_dir, "data"))
    except Exception:
        pass

    try:                                   # data lama (StintLab) → YurLaps
        from app.config import migrate_legacy_data
        migrate_legacy_data()
    except Exception:
        pass

    from app.main import build_parser

    argv = sys.argv[1:]
    known = {"--native", "--web", "--desktop", "--app-window", "--browser",
             "--no-browser", "--host", "--port", "--endpoint", "--data-dir",
             "--open-folder", "--help", "-h"}
    if not any(a.split("=")[0] in known for a in argv):
        # Default .exe: UI native Tkinter — satu jendela Windows sungguhan,
        # tanpa browser, tanpa server web, tanpa firewall prompt.
        # Mode web/TV tetap ada lewat "YurLaps.exe --web".
        argv = ["--native"] + argv
    sys.argv = [sys.argv[0]] + argv
    build_parser()  # validasi awal
    from app.main import main as real_main
    return real_main(argv)


if __name__ == "__main__":
    sys.exit(main())
