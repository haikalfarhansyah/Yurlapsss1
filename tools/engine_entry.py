"""Mesin YurLaps untuk jendela Electron.

Dipakai oleh CI:  pyinstaller tools/engine.spec
Hasil: desktop/engine/YurLapsEngine.exe (+ semua dependensinya).

Proses ini TIDAK membuka jendela sendiri. Ia menjalankan mesin timing dan
server HTTP lokal di 127.0.0.1 (port acak), lalu mencetak satu baris JSON
berisi port tersebut. Shell Electron membaca baris itu dan memuat UI-nya.
"""
import multiprocessing
import sys

from app.main import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main(sys.argv[1:]))
