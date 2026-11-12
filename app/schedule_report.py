"""PDF jadwal event: urutan sesi, jam mulai, dan peserta per grup."""
from io import BytesIO
from datetime import datetime
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

from .race_report import table


def clock(ms):
    """Jam mulai sesi (HH:MM) dari epoch milidetik."""
    if ms is None:
        return "-"
    try:
        return datetime.fromtimestamp(float(ms) / 1000).strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        return "-"


def span(ms):
    """Durasi sesi dalam menit."""
    if not ms:
        return "-"
    return f"{int(round(ms / 60000))} mnt"


def driver_names(drivers):
    names = [str(d.get("nick") or d.get("name") or "") for d in drivers or []
             if d.get("nick") or d.get("name")]
    return ", ".join(names) if names else "-"


def render_schedule_pdf(event, schedule):
    """Susun PDF jadwal event dari dokumen event dan hasil perhitungan jadwal."""
    stream = BytesIO()
    width, height = landscape(A4)
    usable = width - 56
    doc = SimpleDocTemplate(stream, pagesize=(width, height), leftMargin=28, rightMargin=28,
                            topMargin=68, bottomMargin=32)
    styles = getSampleStyleSheet()
    styles["Normal"].fontSize = 8
    styles["Normal"].leading = 10
    rows = schedule.get("rows") or []
    story = []

    def para(text, style="Normal"):
        return Paragraph(escape(str(text)), styles[style])

    def heading(title):
        title_flow = para(title, "Heading3")
        title_flow.keepWithNext = True
        spacer = Spacer(1, 5)
        spacer.keepWithNext = True
        story.extend([title_flow, spacer])

    heading("JADWAL SESI")
    data = [["NO", "MULAI", "SESI", "KELAS", "GRUP", "PEMBALAP", "DURASI", "SUMBER PESERTA"]]
    for index, row in enumerate(rows, 1):
        data.append([
            index,
            clock(row.get("start_ms")),
            Paragraph(escape(str(row.get("name") or "")), styles["Normal"]),
            Paragraph(escape(str(row.get("class_name") or "")), styles["Normal"]),
            Paragraph(escape(str(row.get("group") or "")), styles["Normal"]),
            Paragraph(escape(driver_names(row.get("drivers"))), styles["Normal"]),
            span(row.get("duration_ms")),
            Paragraph(escape(str(row.get("seed_source") or "Roster")), styles["Normal"]),
        ])
    if len(data) == 1:
        data.append(["-", "-", "Belum ada sesi terjadwal", "-", "-", "-", "-", "-"])
    weights = [24, 40, 110, 90, 60, 210, 45, 120]
    story += [table(data, [usable * value / sum(weights) for value in weights], 6.6), Spacer(1, 12)]

    scheme = (schedule.get("scheme") or {}).get("type", "sequential")
    order = ("satu per satu (kelas demi kelas)" if scheme == "sequential"
             else "per ronde (semua kelas di ronde yang sama berurutan)")
    start_at = schedule.get("start_at")
    story.append(para(
        f"Urutan: {order}. Jam mulai event: "
        f"{datetime.fromtimestamp(float(start_at)).strftime('%d %b %Y %H:%M') if start_at else 'belum diatur'}. "
        "Waktu sesi memakai jam lokal komputer operator."))
    story.append(para(
        "Sesi tanpa jam mulai berarti jam mulai event belum diisi. Durasi diambil dari target preset race; "
        "sesi dengan target jumlah lap memakai estimasi waktu lap pada rencana."))

    def decorate(canvas, doc):
        canvas.setFillColor(colors.HexColor("#f7f8fa"))
        canvas.setStrokeColor(colors.HexColor("#dce0e5"))
        canvas.roundRect(28, height - 55, width - 56, 36, 6, fill=1, stroke=1)
        canvas.setFillColor(colors.HexColor("#182b40"))
        canvas.setFont("Helvetica-Bold", 12)
        canvas.drawString(40, height - 34, str(event.get("name", "Event"))[:85])
        canvas.setFont("Helvetica", 8)
        stamp = datetime.fromtimestamp(float(start_at)).strftime("%d %b %Y %H:%M") if start_at else "-"
        detail = (f"{event.get('track_name') or 'Track'} | {event.get('date') or '-'} | "
                  f"{len(event.get('classes') or [])} kelas | {len(rows)} sesi | mulai {stamp}")
        canvas.drawString(40, height - 47, detail[:145])
        canvas.setFillColor(colors.HexColor("#65758a"))
        canvas.setFont("Helvetica", 7)
        canvas.drawString(28, 16, "YurLaps | Jadwal event | Urutan, jam mulai, dan peserta per sesi"[:155])
        canvas.drawRightString(width - 28, 16, str(doc.page))

    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return stream.getvalue()
