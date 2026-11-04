"""Uji UI YurLaps end-to-end (Playwright): 3 menu, pensil, driver, gain, log ms."""
import asyncio, sys, json, urllib.request
from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8125"

async def main():
    ok = []
    def check(cond, msg):
        print(("  ok   " if cond else "  FAIL ") + msg)
        ok.append(bool(cond))

    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        p = await b.new_page(viewport={"width": 1280, "height": 860})
        errs = []
        p.on("pageerror", lambda e: errs.append(str(e)))
        await p.goto(BASE + "/", wait_until="load")
        await p.wait_for_timeout(1800)

        title = await p.title()
        check("YurLaps" in title, f"judul halaman memakai YurLaps ({title})")
        logo = await p.evaluate("()=>{const i=document.querySelector('.brand img');"
                                "return i?[i.naturalWidth,i.clientWidth]:null}")
        check(bool(logo and logo[0] > 0 and logo[1] > 40), f"logo tampil ({logo})")
        tabs = await p.eval_on_selector_all(".tab", "els=>els.map(e=>e.textContent.trim())")
        check(tabs == ["Practice", "Driver", "Decoder"], f"tepat 3 menu: {tabs}")
        check("dongle" not in (await p.content()).lower(), "tidak ada kata 'dongle'")
        check("rtl-sdr" not in (await p.content()).lower(), "tidak ada kata 'RTL-SDR'")

        # Practice: kolom sesuai
        heads = await p.eval_on_selector_all("#v-practice th", "els=>els.map(e=>e.textContent.trim())")
        check(heads == ["POS", "PEMBALAP", "LAP", "TERAKHIR", "BEST"], f"kolom Practice: {heads}")
        # tanpa panel terlarang
        html = await p.content()
        for bad in ("Waktu sesi", "Akhiri", "rata-rata", "KONSISTENSI"):
            check(bad not in html, f"Practice tanpa '{bad}'")

        # Decoder: kolom log
        await p.click('.tab[data-view="decoder"]'); await p.wait_for_timeout(600)
        pheads = await p.eval_on_selector_all("#v-decoder table.pass th",
                                              "els=>els.map(e=>e.textContent.trim())")
        check(pheads == ["ID", "TIMESTAMP DECODER (MS)", "RSSI", "PASSING"],
              f"kolom log Decoder: {pheads}")
        # gain: klik preset -> terkirim
        await p.click('#presets button[data-g="28"]'); await p.wait_for_timeout(1200)
        check((await p.inner_text("#gVal")).startswith("28"), "preset gain 28 dB aktif")
        st = json.load(urllib.request.urlopen(BASE + "/api/state"))
        check(abs(float(st["settings"]["decoder_gain"]) - 28) < 0.01,
              f"gain tersimpan di server ({st['settings']['decoder_gain']})")

        # Driver: tambah, simpan, muncul di Practice
        await p.click('.tab[data-view="driver"]'); await p.wait_for_timeout(300)
        await p.click("#btnAdd"); await p.wait_for_timeout(300)
        ins = await p.query_selector_all('#drivers tr input[data-f]')
        await ins[0].fill("7770001"); await ins[1].fill("Uji Otomatis")
        await p.click('#drivers tr:last-child [data-act="save"]')
        await p.wait_for_timeout(1200)
        names = await p.eval_on_selector_all("#drivers .idcell", "els=>els.map(e=>e.textContent)")
        check("7770001" in names, f"driver tersimpan tampil di menu Driver: {names}")
        await p.click('.tab[data-view="practice"]'); await p.wait_for_timeout(800)
        board = await p.inner_text("#board")
        check("Uji Otomatis" in board, "nama tersimpan muncul di Practice")

        # pensil: transponder tanpa nama -> dialog -> simpan
        # (hanya relevan kalau ada transponder yang belum punya nama)
        has_pencil = await p.query_selector(".pencil")
        if not has_pencil:
            print("  ok   (dilewati) belum ada transponder tanpa nama di instance ini")
        if has_pencil:
            await has_pencil.click(); await p.wait_for_timeout(400)
            await p.fill("#mName", "Nama Dari Pensil")
            await p.click("#mSave"); await p.wait_for_timeout(1200)
            board = await p.inner_text("#board")
            check("Nama Dari Pensil" in board, "nama dari dialog pensil muncul di Practice")

        check(not errs, f"tidak ada error JS ({errs[:2]})")
        await b.close()
    print("\n" + ("SEMUA UJI UI LULUS [OK]" if all(ok) else "ADA UJI UI YANG GAGAL"))
    return 0 if all(ok) else 1

sys.exit(asyncio.run(main()))
