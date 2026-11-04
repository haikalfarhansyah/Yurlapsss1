"""YurLaps versi native (Tkinter) — tanpa browser, tanpa server web.

Kenapa Tkinter?
  * Ikut di dalam Python standar: tidak ada paket tambahan, tidak ada WebView2,
    tidak ada Edge/Chrome. Artinya .exe yang dihasilkan benar-benar aplikasi
    Windows biasa, dan jalan di Windows 7/8/8.1/10/11 (Python 3.11 + Tk 8.6).
  * Satu proses, satu jendela: tutup jendela = aplikasi berhenti.

Arsitektur:
  * Logika tetap 100% milik mesin yang sudah ada (Engine, Storage, DecoderClient,
    DecoderManager, Updater) — file ini hanya lapisan TAMPILAN.
  * Komunikasi dengan mesin lewat antrean (queue). Thread mesin tidak pernah
    menyentuh widget Tk; Tk hanya membaca antrean tiap 150 ms (aman, tidak
    "freeze" saat decoder restart / SDR dicabut).
"""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Optional

from .config import APP_NAME, APP_VERSION

# ---------------------------------------------------------------------------
# palet (sama dengan desain clean yang sudah disetujui)
# ---------------------------------------------------------------------------
C = {
    "bg": "#f7f9fc", "card": "#ffffff", "line": "#e7edf4", "line2": "#f0f4f9",
    "ink": "#0d2b45", "txt": "#2a3a4d", "muted": "#8a98aa", "muted2": "#a9b5c3",
    "blue": "#009de0", "blue_l": "#eaf6fe", "blue_xl": "#f5fbff",
    "ok": "#12b76a", "warn": "#d99a2b", "bad": "#e8544c",
}
FONT = "Segoe UI"
GAIN_PRESETS = (0, 8.7, 16.6, 20, 28, 32, 40, 49.6)


def _fmt_lap(ms: Optional[int]) -> str:
    """Waktu lap gaya klasik: 8.986 atau 1:02.334"""
    if ms is None or ms <= 0:
        return "—"
    m, rest = divmod(int(ms), 60000)
    s, t = divmod(rest, 1000)
    return f"{m}:{s:02d}.{t:03d}" if m else f"{s}.{t:03d}"


def _fmt_num(v, dec: int = 2, dash: str = "—") -> str:
    if v is None:
        return dash
    return f"{float(v):.{dec}f}".replace(".", ",")


def _fmt_gain(g: float) -> str:
    return f"{g:g}".replace(".", ",")


class YurLapsWindow:
    """Jendela utama: tiga halaman (Practice · Driver · Decoder)."""

    # ------------------------------------------------------------------ init
    def __init__(self, root: tk.Tk, app):
        self.root = root
        self.app = app
        self.engine = app.engine
        self.cfg = app.cfg
        self.storage = app.storage
        self.events: "queue.Queue[dict]" = queue.Queue()
        self.page = "practice"
        self._sound_on = False
        self._last_lap_count = 0
        self._edit_tid: Optional[int] = None     # baris Driver yang sedang diedit
        self._draft_tid: Optional[int] = None
        self._gain_job: Optional[str] = None
        self._modal: Optional[tk.Toplevel] = None

        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.configure(bg=C["bg"])
        self.root.geometry("1180x760")
        self.root.minsize(940, 620)
        try:
            self.root.iconbitmap(default=str(self._icon_path()))
        except Exception:
            pass

        self._init_fonts()
        self._init_style()
        self._build_shell()
        self._show_page("practice")

        # mesin → UI: event dikirim lewat antrean, Tk yang mengambil
        self.engine.on_event = self._on_engine_event
        self.decoder = app.decoder
        if self.decoder is not None:
            self.decoder.on_passing = self._on_passing

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(150, self._pump)

    # ------------------------------------------------------------------ util
    def _icon_path(self):
        from .config import app_root
        return app_root() / "assets" / "yurlaps.ico"

    def _init_fonts(self):
        self.f_h1 = tkfont.Font(family=FONT, size=16, weight="bold")
        self.f_h2 = tkfont.Font(family=FONT, size=10, weight="bold")
        self.f_body = tkfont.Font(family=FONT, size=10)
        self.f_small = tkfont.Font(family=FONT, size=8)
        self.f_cap = tkfont.Font(family=FONT, size=7, weight="bold")
        self.f_big = tkfont.Font(family=FONT, size=20, weight="bold")
        self.f_mono = tkfont.Font(family="Consolas", size=9)

    def _init_style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")          # paling bisa diwarnai & sama di semua Windows
        except tk.TclError:
            pass
        st.configure(".", background=C["bg"], foreground=C["txt"], font=self.f_body)
        st.configure("TFrame", background=C["bg"])
        st.configure("Card.TFrame", background=C["card"])
        st.configure("TLabel", background=C["bg"], foreground=C["txt"])
        st.configure("Card.TLabel", background=C["card"], foreground=C["txt"])
        st.configure("H1.TLabel", background=C["bg"], foreground=C["ink"], font=self.f_h1)
        st.configure("Cap.TLabel", background=C["card"], foreground=C["muted2"],
                     font=self.f_cap)
        st.configure("Muted.TLabel", background=C["card"], foreground=C["muted"],
                     font=self.f_small)
        st.configure("Name.TLabel", background=C["card"], foreground=C["ink"],
                     font=(FONT, 11, "bold"))
        st.configure("Tid.TLabel", background=C["card"], foreground=C["muted"],
                     font=(FONT, 8))
        st.configure("Best.TLabel", background=C["card"], foreground=C["blue"],
                     font=(FONT, 10, "bold"))
        st.configure("Big.TLabel", background=C["card"], foreground=C["ink"],
                     font=self.f_big)

        # tabel
        st.configure("Yur.Treeview", background=C["card"], fieldbackground=C["card"],
                     foreground=C["txt"], borderwidth=0, rowheight=42,
                     font=(FONT, 10))
        st.configure("Yur.Treeview.Heading", background=C["card"],
                     foreground=C["muted2"], font=self.f_cap, relief="flat",
                     borderwidth=0, padding=(10, 10))
        st.map("Yur.Treeview", background=[("selected", C["blue_l"])],
               foreground=[("selected", C["ink"])])
        st.map("Yur.Treeview.Heading", background=[("active", C["card"])])
        st.configure("Yur.Treeview.Item", padding=(10, 2))

        # tombol
        st.configure("Yur.TButton", background=C["card"], foreground="#5f7288",
                     borderwidth=1, relief="flat", padding=(12, 7),
                     font=(FONT, 9, "bold"))
        st.map("Yur.TButton", background=[("active", "#f2f7fc")])
        st.configure("Primary.TButton", background=C["blue"], foreground="#ffffff",
                     borderwidth=0, relief="flat", padding=(14, 8),
                     font=(FONT, 9, "bold"))
        st.map("Primary.TButton", background=[("active", "#0089c6")])
        st.configure("Link.TButton", background=C["bg"], foreground=C["muted"],
                     borderwidth=0, relief="flat", font=(FONT, 9))
        st.map("Link.TButton", background=[("active", C["bg"])])
        st.configure("Pill.TButton", background=C["card"], foreground="#61758c",
                     borderwidth=1, relief="flat", padding=(8, 4),
                     font=(FONT, 8, "bold"), width=4)
        st.map("Pill.TButton", background=[("active", C["blue_xl"])])

        # slider + entry
        st.configure("Yur.Horizontal.TScale", background=C["card"],
                     troughcolor=C["line"], borderwidth=0)
        st.configure("Yur.TEntry", fieldbackground="#f8fafd", bordercolor=C["line"],
                     lightcolor=C["line"], darkcolor=C["line"], padding=6)
        st.configure("Nav.TButton", background=C["card"], foreground="#78879a",
                     borderwidth=0, relief="flat", padding=(14, 9),
                     font=(FONT, 10), anchor="w")
        st.map("Nav.TButton", background=[("active", "#f5f8fc")])
        st.configure("Sound.TButton", background=C["card"], foreground="#61758c",
                     borderwidth=1, relief="flat", padding=(10, 4),
                     font=(FONT, 9, "bold"))
        st.map("Sound.TButton", background=[("active", C["blue_xl"])])
        st.configure("NavOn.TButton", background=C["blue_l"], foreground=C["blue"],
                     borderwidth=0, relief="flat", padding=(14, 9),
                     font=(FONT, 10, "bold"), anchor="w")
        st.map("NavOn.TButton", background=[("active", C["blue_l"])])

    # ------------------------------------------------------------------ shell
    def _build_shell(self):
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)

        # ----- sidebar
        side = tk.Frame(outer, bg=C["card"], width=200)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        brand = tk.Frame(side, bg=C["card"])
        brand.pack(fill="x", padx=14, pady=(20, 0))
        try:                          # logo asli (PNG) kalau ada
            from pathlib import Path
            p = Path(__file__).parent / "static" / "assets" / "yurlaps-logo.png"
            if not p.is_file():
                from .config import app_root
                p = app_root() / "assets" / "yurlaps-logo.png"
            img = tk.PhotoImage(file=str(p))
            scale = max(1, img.width() // 150)
            self._logo = img.subsample(scale, scale)
            tk.Label(brand, image=self._logo, bg=C["card"]).pack(anchor="w")
        except Exception:             # cadangan: teks
            tk.Label(brand, text="YurLaps", bg=C["card"], fg=C["ink"],
                     font=(FONT, 17, "bold italic")).pack(anchor="w")
        tk.Label(brand, text="RC TIMING SYSTEM", bg=C["card"], fg=C["muted2"],
                 font=self.f_cap).pack(anchor="w", pady=(2, 16))

        self._nav = {}
        for key, label in (("practice", "Practice"), ("driver", "Driver"),
                           ("decoder", "Decoder")):
            b = ttk.Button(side, text="   " + label, style="Nav.TButton",
                           command=lambda k=key: self._show_page(k))
            b.pack(fill="x", padx=8, pady=1)
            self._nav[key] = b

        foot = tk.Frame(side, bg=C["card"])
        foot.pack(side="bottom", fill="x", padx=14, pady=14)
        self.lbl_conn = tk.Label(foot, text="Decoder offline", bg=C["card"],
                                 fg=C["muted"], font=(FONT, 8, "bold"), anchor="w")
        self.lbl_conn.pack(fill="x")
        self.lbl_ver = tk.Label(foot, text=f"{APP_NAME} v{APP_VERSION}", bg=C["card"],
                                fg=C["muted2"], font=(FONT, 8), anchor="w")
        self.lbl_ver.pack(fill="x")
        self.lbl_lan = tk.Label(foot, text="", bg=C["card"], fg=C["muted2"],
                                font=(FONT, 7), anchor="w", justify="left")
        self.lbl_lan.pack(fill="x", pady=(4, 0))

        # ----- area halaman
        self.body = tk.Frame(outer, bg=C["bg"])
        self.body.pack(side="left", fill="both", expand=True)

        self.pages = {}
        self.pages["practice"] = self._build_practice(self.body)
        self.pages["driver"] = self._build_driver(self.body)
        self.pages["decoder"] = self._build_decoder(self.body)

    def _card(self, parent, **pack):
        wrap = tk.Frame(parent, bg=C["card"], highlightbackground=C["line"],
                        highlightthickness=1)
        wrap.pack(**pack)
        return wrap

    # --------------------------------------------------------------- practice
    def _build_practice(self, parent):
        page = tk.Frame(parent, bg=C["bg"])

        head = tk.Frame(page, bg=C["bg"])
        head.pack(fill="x", padx=26, pady=(22, 12))
        ttk.Label(head, text="Practice", style="H1.TLabel").pack(side="left")
        ttk.Button(head, text="Ekspor CSV", style="Link.TButton",
                   command=self._export_csv).pack(side="right")

        card = self._card(page, fill="x", padx=26)
        cols = ("pos", "nama", "lap", "last", "best")
        self.tv_practice = ttk.Treeview(card, columns=cols, show="headings",
                                        style="Yur.Treeview", height=8)
        for c, text, width, anchor in (
                ("pos", "POS", 56, "center"), ("nama", "PEMBALAP", 380, "w"),
                ("lap", "LAP", 90, "e"), ("last", "TERAKHIR", 130, "e"),
                ("best", "BEST", 130, "e")):
            self.tv_practice.heading(c, text=text)
            self.tv_practice.column(c, width=width, anchor=anchor,
                                    stretch=(c == "nama"))
        self.tv_practice.tag_configure("best", foreground=C["blue"])
        self.tv_practice.pack(fill="x", padx=10, pady=(1, 0))
        self.empty_practice = tk.Label(
            card, text="Belum ada lintasan.\nBegitu transponder lewat, namanya "
                       "muncul di sini.", bg=C["card"], fg=C["muted"],
            font=(FONT, 10), justify="center")
        self.tv_practice.bind("<Button-1>", self._practice_click)

        foot = tk.Frame(card, bg=C["card"])
        foot.pack(fill="x", padx=16, pady=10)
        self.lbl_p_drv = tk.Label(foot, text="0 pembalap", bg=C["card"], fg=C["ink"],
                                  font=(FONT, 9, "bold"))
        self.lbl_p_drv.pack(side="left")
        self.lbl_p_lap = tk.Label(foot, text="   0 putaran", bg=C["card"], fg=C["ink"],
                                  font=(FONT, 9, "bold"))
        self.lbl_p_lap.pack(side="left")
        self.lbl_p_best = tk.Label(foot, text="   Best sesi —", bg=C["card"],
                                   fg=C["blue"], font=(FONT, 9, "bold"))
        self.lbl_p_best.pack(side="left")
        ttk.Button(foot, text="Suara: mati", style="Sound.TButton",
                   command=self._toggle_sound).pack(side="right")
        self.btn_sound = foot.winfo_children()[-1]

        tk.Label(page, bg=C["bg"], text="Klik baris transponder tanpa nama untuk "
                 "menulis nama (ikon pensil muncul di kolom pembalap).",
                 fg=C["muted2"], font=self.f_small).pack(anchor="w", padx=27, pady=(8, 0))
        return page

    # ----------------------------------------------------------------- driver
    def _build_driver(self, parent):
        page = tk.Frame(parent, bg=C["bg"])

        head = tk.Frame(page, bg=C["bg"])
        head.pack(fill="x", padx=26, pady=(22, 12))
        ttk.Label(head, text="Driver", style="H1.TLabel").pack(side="left")
        ttk.Button(head, text="+ Tambah driver", style="Primary.TButton",
                   command=self._add_driver).pack(side="right")

        card = self._card(page, fill="both", expand=True, padx=26)
        cols = ("tid", "nama", "aksi")
        self.tv_driver = ttk.Treeview(card, columns=cols, show="headings",
                                     style="Yur.Treeview", height=12)
        for c, text, width, anchor in (("tid", "TRANSPONDER ID", 180, "w"),
                                       ("nama", "NAMA", 420, "w"),
                                       ("aksi", "", 180, "e")):
            self.tv_driver.heading(c, text=text)
            self.tv_driver.column(c, width=width, anchor=anchor,
                                  stretch=(c == "nama"))
        self.tv_driver.pack(fill="both", expand=True, padx=10, pady=(1, 0))
        self.empty_driver = tk.Label(
            card, text="Belum ada driver tersimpan.\nKlik + Tambah driver, atau "
                       "tulis nama lewat ikon pensil di Practice.",
            bg=C["card"], fg=C["muted"], font=(FONT, 10), justify="center")

        act = tk.Frame(card, bg=C["card"])
        act.pack(fill="x", padx=16, pady=10)
        self.lbl_d_hint = tk.Label(
            act, text="Pilih baris lalu Edit / Hapus. Nama yang disimpan langsung "
                      "dipakai di Practice.", bg=C["card"], fg=C["muted"],
            font=self.f_small)
        self.lbl_d_hint.pack(side="left")
        ttk.Button(act, text="Hapus", style="Yur.TButton",
                   command=self._delete_driver).pack(side="right", padx=(6, 0))
        ttk.Button(act, text="Edit", style="Yur.TButton",
                   command=self._edit_driver).pack(side="right")
        return page

    # ---------------------------------------------------------------- decoder
    def _build_decoder(self, parent):
        page = tk.Frame(parent, bg=C["bg"])
        head = tk.Frame(page, bg=C["bg"])
        head.pack(fill="x", padx=26, pady=(22, 12))
        ttk.Label(head, text="Decoder", style="H1.TLabel").pack(side="left")

        row = tk.Frame(page, bg=C["bg"])
        row.pack(fill="x", padx=26)

        stat = tk.Frame(row, bg=C["card"], highlightbackground=C["line"],
                        highlightthickness=1)
        stat.pack(side="left", fill="x", expand=True)
        inner = tk.Frame(stat, bg=C["card"])
        inner.pack(fill="x", padx=18, pady=16)
        self.led = tk.Canvas(inner, width=26, height=26, bg=C["card"],
                             highlightthickness=0)
        self.led.pack(side="left")
        self._led_item = self.led.create_oval(7, 7, 19, 19, fill=C["bad"], outline="")
        txt = tk.Frame(inner, bg=C["card"])
        txt.pack(side="left", padx=(6, 0))
        self.lbl_state = tk.Label(txt, text="Tidak terhubung", bg=C["card"],
                                  fg=C["ink"], font=(FONT, 11, "bold"), anchor="w")
        self.lbl_state.pack(fill="x")
        self.lbl_state_sub = tk.Label(txt, text="", bg=C["card"], fg=C["muted"],
                                      font=self.f_small, anchor="w", justify="left",
                                      wraplength=420)
        self.lbl_state_sub.pack(fill="x")

        metric = tk.Frame(row, bg=C["card"], highlightbackground=C["line"],
                          highlightthickness=1)
        metric.pack(side="left", padx=(14, 0))
        mi = tk.Frame(metric, bg=C["card"])
        mi.pack(padx=18, pady=16, anchor="w")
        tk.Label(mi, text="NOISE", bg=C["card"], fg=C["muted2"],
                 font=self.f_cap).pack(anchor="w")
        self.lbl_noise = tk.Label(mi, text="—  dBFS", bg=C["card"], fg=C["ink"],
                                  font=self.f_big)
        self.lbl_noise.pack(anchor="w")

        # gain
        gcard = self._card(page, fill="x", padx=26, pady=(14, 0))
        gi = tk.Frame(gcard, bg=C["card"])
        gi.pack(fill="x", padx=18, pady=(14, 4))
        tk.Label(gi, text="GAIN TUNER", bg=C["card"], fg=C["muted2"],
                 font=self.f_cap).pack(side="left")
        self.lbl_gain = tk.Label(gi, text="20 dB", bg=C["card"], fg=C["ink"],
                                 font=self.f_big)
        self.lbl_gain.pack(side="right")

        self.var_gain = tk.DoubleVar(value=float(self.cfg.decoder_gain or 20))
        self.scale = ttk.Scale(gcard, from_=0, to=49.6, variable=self.var_gain,
                               style="Yur.Horizontal.TScale",
                               command=self._on_gain_move)
        self.scale.pack(fill="x", padx=18)
        self.scale.bind("<ButtonRelease-1>", lambda e: self._send_gain())

        presets = tk.Frame(gcard, bg=C["card"])
        presets.pack(fill="x", padx=16, pady=(6, 12))
        self._preset_btns = {}
        for g in GAIN_PRESETS:
            b = ttk.Button(presets, text=_fmt_gain(g), style="Pill.TButton",
                           command=lambda v=g: self._set_gain(v))
            b.pack(side="left", padx=(0, 5))
            self._preset_btns[g] = b
        tk.Label(gcard, text="Lepas slider → YurLaps Decoder dijalankan ulang "
                             "dengan gain baru.", bg=C["card"], fg=C["muted"],
                 font=self.f_small).pack(anchor="w", padx=18, pady=(0, 12))

        # log lintasan
        lcard = self._card(page, fill="both", expand=True, padx=26, pady=(14, 20))
        lh = tk.Frame(lcard, bg=C["card"])
        lh.pack(fill="x", padx=18, pady=(14, 6))
        tk.Label(lh, text="Log aktivitas", bg=C["card"], fg=C["ink"],
                 font=self.f_h2).pack(side="left")
        tk.Label(lh, text="ms sejak YurLaps Decoder dijalankan · 20 lintasan "
                          "terakhir", bg=C["card"], fg=C["muted"],
                 font=self.f_small).pack(side="right")

        cols = ("id", "ts", "rssi", "dur")
        self.tv_passes = ttk.Treeview(lcard, columns=cols, show="headings",
                                      style="Yur.Treeview", height=7)
        for c, text, width, anchor in (("id", "ID", 300, "w"),
                                       ("ts", "TIMESTAMP DECODER (MS)", 240, "w"),
                                       ("rssi", "RSSI", 110, "e"),
                                       ("dur", "PASSING", 110, "e")):
            self.tv_passes.heading(c, text=text)
            self.tv_passes.column(c, width=width, anchor=anchor,
                                  stretch=(c in ("id", "ts")))
        self.tv_passes.pack(fill="both", expand=True, padx=10, pady=(0, 1))
        self.empty_passes = tk.Label(
            lcard, text="Belum ada lintasan.\nLog ini terisi begitu YurLaps "
                        "Decoder mengirim data.", bg=C["card"], fg=C["muted"],
            font=(FONT, 10), justify="center")
        return page

    # ---------------------------------------------------------------- navigasi
    def _show_page(self, key: str):
        self.page = key
        for k, page in self.pages.items():
            page.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        for k, btn in self._nav.items():
            btn.configure(style="NavOn.TButton" if k == key else "Nav.TButton")
        if key == "practice":
            self.render_practice()
        elif key == "driver":
            self.render_drivers()
        else:
            self.render_decoder()

    # ------------------------------------------------------------------ data
    def _palette(self):
        if self.app.supervisor is None:
            return {"state": "off", "message": "Decoder tidak dikelola YurLaps"}
        return self.app.supervisor.status.to_dict()

    # --------------------------------------------------------------- render
    def render_practice(self):
        rows = sorted(self.engine.drivers.values(),
                      key=lambda d: (d.best_lap is None,
                                     d.best_lap.time_ms if d.best_lap else 0))
        self.tv_practice.delete(*self.tv_practice.get_children())
        for i, d in enumerate(rows, start=1):
            named = bool((d.driver or "").strip())
            nama = d.driver if named else f"{d.transponder_id}    ✎  tulis nama"
            self.tv_practice.insert(
                "", "end", iid=str(d.transponder_id),
                values=(i, nama, d.total_laps or 0,
                        _fmt_lap(d.last_lap_ms),
                        _fmt_lap(d.best_lap.time_ms if d.best_lap else None)),
                tags=("best",))
        if rows:
            self.empty_practice.place_forget()
        else:
            self.empty_practice.place(relx=0.5, rely=0.42, anchor="center")
        best = min((d.best_lap.time_ms for d in rows if d.best_lap), default=None)
        self.lbl_p_drv.configure(text=f"{len(rows)} pembalap")
        self.lbl_p_lap.configure(text=f"   {sum(d.total_laps or 0 for d in rows)} putaran")
        self.lbl_p_best.configure(text=f"   Best sesi {_fmt_lap(best)}")

    def render_drivers(self):
        self.tv_driver.delete(*self.tv_driver.get_children())
        saved = sorted((d for d in self.engine.drivers.values()
                        if (d.driver or "").strip()),
                       key=lambda d: (d.driver or "").lower())
        for d in saved:
            self.tv_driver.insert("", "end", iid=str(d.transponder_id),
                                  values=(d.transponder_id, d.driver,
                                          "Edit    Hapus"))
        if not saved:
            self.empty_driver.place(relx=0.5, rely=0.4, anchor="center")
            self.lbl_d_hint.configure(
                text="Belum ada driver tersimpan. Klik + Tambah driver, atau tulis "
                     "nama lewat ikon pensil di menu Practice.")
        else:
            self.empty_driver.place_forget()
            self.lbl_d_hint.configure(
                text="Pilih baris lalu Edit / Hapus. Nama yang disimpan langsung "
                     "dipakai di Practice.")

    def render_decoder(self):
        mgr = self._palette()
        online = bool(self.engine.decoder_online)
        waiting = mgr.get("state") == "waiting" or "menunggu" in (
            mgr.get("message") or "").lower()
        color = C["ok"] if online else (C["warn"] if waiting else C["bad"])
        self.led.itemconfigure(self._led_item, fill=color)
        self.lbl_state.configure(
            text="Terhubung" if online else
                 ("Menunggu YurLaps Decoder" if waiting else "Tidak terhubung"))
        sub = (f"{self.cfg.decoder_name} · {self.cfg.zmq_endpoint}" if online
               else (mgr.get("message") or "YurLaps Decoder belum terhubung"))
        self.lbl_state_sub.configure(text=sub[:110])

        noise = self.engine.noise_history[-1][1] if self.engine.noise_history else None
        self.lbl_noise.configure(text=f"{_fmt_num(noise, 1)}  dBFS")
        self.lbl_conn.configure(text="Decoder terhubung" if online
                                else "Decoder offline")

        passes = list(self.engine.pass_log)[-20:][::-1]
        self.tv_passes.delete(*self.tv_passes.get_children())
        for p in passes:
            named = (p.get("driver") or "").strip()
            who = f"{p['transponder_id']} · {named}" if named else str(p["transponder_id"])
            ts = p.get("decoder_timecode")
            dur = p.get("pass_duration_us")
            self.tv_passes.insert("", "end", values=(
                who, f"{ts} ms" if ts is not None else "—",
                _fmt_num(p.get("rssi")),
                f"{_fmt_num((dur or 0) / 1000, 1)} ms" if dur else "—"))
        if passes:
            self.empty_passes.place_forget()
        else:
            self.empty_passes.place(relx=0.5, rely=0.5, anchor="center")
        if self.page == "decoder":
            self._paint_gain(float(self.cfg.decoder_gain or 20))

    # ------------------------------------------------------------ antrean UI
    def _on_engine_event(self, ev: dict) -> None:
        self.events.put(ev)

    def _on_passing(self, *a) -> None:          # dipanggil dari thread decoder
        self.events.put({"kind": "__passing__"})

    def _pump(self) -> None:
        """Ambil event mesin tiap 150 ms. Satu-satunya jalur ke widget Tk."""
        dirty = set()
        try:
            while True:
                ev = self.events.get_nowait()
                dirty.add(ev.get("kind", ""))
        except queue.Empty:
            pass
        if dirty:
            if "__passing__" in dirty or "passing" in dirty or "lap" in dirty:
                self.render_practice()
                self._maybe_beep()
                if self.page == "decoder":
                    self.render_decoder()
            if "driver" in dirty:
                self.render_practice()
                if self.page == "driver":
                    self.render_drivers()
            if dirty & {"decoder", "log", "stint"} and self.page == "decoder":
                self.render_decoder()
            if "log" in dirty:
                self.lbl_conn.configure(
                    text="Decoder terhubung" if self.engine.decoder_online
                    else "Decoder offline")
        self.root.after(150, self._pump)

    def _maybe_beep(self) -> None:
        total = sum(d.total_laps or 0 for d in self.engine.drivers.values())
        if total > self._last_lap_count and self._sound_on:
            try:
                import winsound
                winsound.Beep(1180, 70)
            except Exception:
                self.root.bell()
        self._last_lap_count = total

    def _toggle_sound(self) -> None:
        self._sound_on = not self._sound_on
        self.btn_sound.configure(text="Suara: nyala" if self._sound_on
                                 else "Suara: mati")

    # ---------------------------------------------------------------- aksi UI
    def _practice_click(self, event) -> None:
        row = self.tv_practice.identify_row(event.y)
        if not row or not row.isdigit():
            return
        d = self.engine.drivers.get(int(row))
        if d is None or (d.driver or "").strip():
            if d is not None:            # sudah bernama → buka untuk ubah
                self._name_dialog(d.transponder_id, d.driver)
            return
        self._name_dialog(d.transponder_id, "")

    def _name_dialog(self, tid: int, current: str) -> None:
        if self._modal is not None:
            try:
                self._modal.destroy()
            except Exception:
                pass
        win = tk.Toplevel(self.root, bg=C["card"])
        self._modal = win
        win.title("Nama pembalap")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        tk.Label(win, text="Tulis nama pembalap", bg=C["card"], fg=C["ink"],
                 font=(FONT, 12, "bold")).pack(anchor="w", padx=18, pady=(16, 2))
        tk.Label(win, text=f"Transponder {tid}", bg=C["card"], fg=C["muted"],
                 font=self.f_small).pack(anchor="w", padx=18)
        entry = ttk.Entry(win, style="Yur.TEntry", width=34, font=(FONT, 11))
        entry.insert(0, current or "")
        entry.pack(padx=18, pady=(10, 0), fill="x")
        entry.focus_set()
        entry.select_range(0, "end")

        btns = tk.Frame(win, bg=C["card"])
        btns.pack(fill="x", padx=18, pady=(14, 16))

        def save(_=None):
            name = entry.get().strip()
            if not name:
                entry.configure(foreground=C["bad"])
                return
            self.engine.update_driver(tid, driver=name)
            self.events.put({"kind": "driver"})
            win.destroy()
            self._modal = None

        def close(_=None):
            win.destroy()
            self._modal = None

        ttk.Button(btns, text="Batal", style="Yur.TButton",
                   command=close).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="Simpan", style="Primary.TButton",
                   command=save).pack(side="right")
        win.bind("<Return>", save)
        win.bind("<Escape>", close)
        win.update_idletasks()
        win.geometry(f"+{self.root.winfo_rootx() + 260}+{self.root.winfo_rooty() + 210}")

    def _selected_driver(self) -> Optional[int]:
        sel = self.tv_driver.selection()
        return int(sel[0]) if sel else None

    def _add_driver(self) -> None:
        win = tk.Toplevel(self.root, bg=C["card"])
        win.title("Tambah driver")
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)
        fields = {}
        for key, label, default in (("tid", "TRANSPONDER ID", ""),
                                    ("nama", "NAMA", "")):
            tk.Label(win, text=label, bg=C["card"], fg=C["muted2"],
                     font=self.f_cap).pack(anchor="w", padx=18,
                                           pady=(14 if key == "tid" else 8, 2))
            e = ttk.Entry(win, style="Yur.TEntry", width=34, font=(FONT, 11))
            e.insert(0, default)
            e.pack(padx=18, fill="x")
            fields[key] = e
        fields["tid"].focus_set()

        btns = tk.Frame(win, bg=C["card"])
        btns.pack(fill="x", padx=18, pady=(14, 16))

        def save(_=None):
            tid_txt = fields["tid"].get().strip()
            name = fields["nama"].get().strip()
            if not tid_txt.isdigit():
                messagebox.showwarning("Transponder ID", "ID harus berupa angka.",
                                       parent=win)
                return
            if not name:
                messagebox.showwarning("Nama", "Nama pembalap belum diisi.",
                                       parent=win)
                return
            tid = int(tid_txt)
            existing = self.engine.drivers.get(tid)
            if existing is not None and (existing.driver or "").strip():
                messagebox.showwarning(
                    "ID sudah dipakai",
                    f"Transponder {tid} sudah dipakai {existing.driver}.",
                    parent=win)
                return
            self.engine.update_driver(tid, driver=name)
            self.events.put({"kind": "driver"})
            win.destroy()

        ttk.Button(btns, text="Batal", style="Yur.TButton",
                   command=win.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="Simpan", style="Primary.TButton",
                   command=save).pack(side="right")
        win.bind("<Return>", save)
        win.bind("<Escape>", lambda e: win.destroy())
        win.update_idletasks()
        win.geometry(f"+{self.root.winfo_rootx() + 260}+{self.root.winfo_rooty() + 180}")

    def _edit_driver(self) -> None:
        tid = self._selected_driver()
        if tid is None:
            messagebox.showinfo("Edit driver", "Pilih dulu baris driver yang mau "
                                "diedit.", parent=self.root)
            return
        d = self.engine.drivers.get(tid)
        self._name_dialog(tid, d.driver if d else "")

    def _delete_driver(self) -> None:
        tid = self._selected_driver()
        if tid is None:
            messagebox.showinfo("Hapus driver", "Pilih dulu baris driver yang mau "
                                "dihapus.", parent=self.root)
            return
        d = self.engine.drivers.get(tid)
        label = (d.driver if d and d.driver else f"#{tid}")
        if not messagebox.askyesno(
                "Hapus driver",
                f"Hapus pemetaan {label}?\n\nLap yang sudah tercatat tetap "
                "tersimpan dan tetap bisa diekspor.", parent=self.root):
            return
        self.engine.remove_driver(tid)
        self.events.put({"kind": "driver"})

    def _export_csv(self) -> None:
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            parent=self.root, defaultextension=".csv",
            initialfile=f"yurlaps-sesi{self.engine.session_id}.csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            import csv
            rows = self.storage.session_rows(self.engine.session_id) if self.storage else []
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow(["waktu", "transponder_id", "tipe", "pembalap", "stint",
                            "lap_ke", "waktu_lap_ms", "waktu_lap", "rssi", "mer",
                            "pass_duration_us"])
                for r in rows:
                    ms = int(r["time_ms"] or 0)
                    w.writerow([time.strftime("%Y-%m-%d %H:%M:%S",
                                              time.localtime(r["at"])),
                                r["transponder_id"], r["transponder_type"] or "",
                                r["driver"] or "", r["stint_index"] or "",
                                r["seq"] or "", ms, _fmt_lap(ms), r["rssi"],
                                r["mer"], r["pass_duration_us"]])
            messagebox.showinfo("Ekspor CSV", f"Tersimpan:\n{path}",
                                parent=self.root)
        except Exception as exc:
            messagebox.showerror("Ekspor CSV", f"Gagal menyimpan: {exc}",
                                 parent=self.root)

    # ------------------------------------------------------------------ gain
    def _paint_gain(self, gain: float) -> None:
        self.lbl_gain.configure(text=f"{_fmt_gain(gain)} dB")
        for g, btn in self._preset_btns.items():
            on = abs(g - gain) < 0.05
            btn.configure(style="Primary.TButton" if on else "Pill.TButton")

    def _on_gain_move(self, value) -> None:
        self.lbl_gain.configure(text=f"{_fmt_gain(round(float(value), 1))} dB")
        if self._gain_job is not None:
            try:
                self.root.after_cancel(self._gain_job)
            except Exception:
                pass
        self._gain_job = self.root.after(450, self._send_gain)

    def _set_gain(self, value: float) -> None:
        """Klik preset: langsung terapkan (tidak menunggu jeda slider)."""
        self.var_gain.set(value)
        self.lbl_gain.configure(text=f"{_fmt_gain(value)} dB")
        self._paint_gain(value)
        if self._gain_job is not None:
            try:
                self.root.after_cancel(self._gain_job)
            except Exception:
                pass
        self._gain_job = None
        self._apply_gain_now(value)

    def _apply_gain_now(self, value: float) -> None:
        mgr = self.app.supervisor
        if mgr is not None:
            mgr.apply_gain(value)
        else:
            self.cfg.decoder_gain = value
            self.cfg.save()

    def _send_gain(self) -> None:
        self._gain_job = None
        value = round(float(self.var_gain.get()), 1)
        mgr = self.app.supervisor
        if mgr is not None:
            mgr.apply_gain(value)          # simpan + restart decoder (debounce)
        else:
            self.cfg.decoder_gain = value
            self.cfg.save()
        self._paint_gain(value)

    # ------------------------------------------------------------------ tutup
    def _on_close(self) -> None:
        try:
            self.root.destroy()
        finally:
            self.app.stop()


# ---------------------------------------------------------------------------
def run_native(args) -> int:
    """Titik masuk mode native: satu jendela Tk, tanpa server web."""
    from .main import YurLaps, _safe_console

    _safe_console()
    app = YurLaps(args)
    app.start_native()                     # mesin + decoder, tanpa HTTP
    root = tk.Tk()
    YurLapsWindow(root, app)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
    return 0
