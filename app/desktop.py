"""Mode desktop: menampilkan UI YurLaps seperti aplikasi.

Tiga tingkatan, dipilih otomatis dari yang paling "native":

1. **Jendela aplikasi (Edge/Chrome `--app`)** — direkomendasikan.
   Membuka UI di jendela tanpa address bar/tab, memakai profil browser
   terpisah, sehingga terlihat dan terasa seperti aplikasi biasa.
   Tidak butuh pywebview maupun pythonnet. Target rilis Windows 10/11.

2. **pywebview** — jendela native sungguhan, TAPI butuh WebView2 + pythonnet
   yang sering gagal saat dibundel PyInstaller ("Failed to resolve
   Python.Runtime.Loader.Initialize"). Karena itu hanya dipakai kalau
   dijalankan dari source dan diminta lewat `--desktop`.

3. **Browser default** — jaring pengaman terakhir.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path
from typing import Optional

IS_WINDOWS = os.name == "nt"


# ---------------------------------------------------------------------------
# pencarian browser berbasis Chromium untuk mode --app
# ---------------------------------------------------------------------------
def browser_candidates() -> list[Path]:
    """Kemungkinan lokasi Edge/Chrome di Windows (urutan prioritas)."""
    if not IS_WINDOWS:
        return []
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LocalAppData", str(Path.home() / "AppData" / "Local"))
    return [
        Path(pf86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(pf) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(pf86) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(local) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    ]


def find_browser() -> Optional[Path]:
    """Browser Chromium pertama yang benar-benar ada (bisa dioverride env)."""
    override = (os.environ.get("YURLAPS_BROWSER") or os.environ.get("STINTLAB_BROWSER", "")).strip()
    if override:
        p = Path(override)
        if p.is_file():
            return p
    for cand in browser_candidates():
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


def launch_app_window(url: str, profile_dir: Optional[Path] = None,
                      size: tuple[int, int] = (1400, 900)) -> bool:
    """Buka UI dalam jendela gaya aplikasi (tanpa tab & address bar)."""
    exe = find_browser()
    if exe is None:
        return False
    args = [
        str(exe),
        f"--app={url}",
        f"--window-size={size[0]},{size[1]}",
        "--no-first-run",
        "--no-default-browser-check",
        "--autoplay-policy=no-user-gesture-required",
    ]
    if profile_dir is not None:
        try:
            profile_dir.mkdir(parents=True, exist_ok=True)
            args.append(f"--user-data-dir={profile_dir}")
        except Exception:
            pass
    try:
        subprocess.Popen(args, close_fds=True)
        return True
    except Exception:
        return False


def _keep_alive(app) -> int:
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def run_desktop(args) -> int:
    from .main import YurLaps, _safe_console  # impor di dalam agar tidak sirkular

    _safe_console()
    app = YurLaps(args)
    # The packaged `--app-window` shell is a local desktop UI. Keep its HTTP
    # engine loopback-only; LAN/TV access remains explicit via `--web`.
    if not getattr(args, "web", False) and not getattr(args, "host", None):
        app.cfg.host = "127.0.0.1"
    _, port = app.start()
    entry = f"http://127.0.0.1:{port}/"
    if app.cfg.access_token:
        entry += f"?t={app.cfg.access_token}"

    if getattr(args, "no_browser", False):
        print(f"YurLaps berjalan: {entry}")
        return _keep_alive(app)

    # 1) pywebview hanya kalau diminta eksplisit (--desktop)
    if getattr(args, "desktop", False):
        try:
            import webview  # type: ignore
        except Exception:
            webview = None
        if webview is not None:
            try:
                webview.create_window(
                    f"YurLaps - {app.cfg.track_name}", entry,
                    width=1280, height=820, min_size=(900, 600),
                    background_color="#0d1117",
                )
                webview.start(debug=False)
                app.stop()
                return 0
            except Exception as exc:
                print(f"Jendela pywebview gagal ({exc}). Memakai jendela browser.")

    # 2) jendela gaya aplikasi via Edge/Chrome
    profile = None
    try:
        profile = app.storage.path.parent / "browser-profile"
    except Exception:
        profile = None
    if launch_app_window(entry, profile):
        try:
            app.engine.log("info", "UI dibuka dalam jendela aplikasi (Edge/Chrome)")
        except Exception:
            pass
        print(f"\n  YurLaps berjalan di jendela aplikasi. Alamat: {entry}")
        print("  Tutup jendela konsol ini untuk menghentikan aplikasi.\n")
        return _keep_alive(app)

    # 3) jaring pengaman: browser default
    print("Browser Chromium (Edge/Chrome) tidak ditemukan.")
    print(f"Membuka di browser default: {entry}")
    try:
        app.engine.log("warn", "Edge/Chrome tidak ditemukan - membuka browser default")
    except Exception:
        pass
    webbrowser.open(entry)
    return _keep_alive(app)
