"""Exports for people: an Excel file with one sheet per tour, and a PDF
sheet per tour for the drivers (everything they need on the road, no map).

The CSV export (csv_io.schedules_to_csv) stays the machine-readable format;
these two are laid out to be read and printed.
"""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
from datetime import date

from tour_insertion.csv_io import format_time
from tour_insertion.load import PICKUP, load_after, max_load
from tour_insertion.model import Schedule, ScheduledStop
from tour_insertion.planner import Unplaced

LABELS = {
    "de": {
        "tour": "Tour", "vehicle": "Fahrzeug", "start": "Start", "end": "Zurück",
        "duration": "Dauer", "stops": "Stopps", "items": "Items", "capacity": "Kapazität",
        "nr": "Nr.", "arrival": "Ankunft", "departure": "Abfahrt", "stop": "Stopp",
        "name": "Name", "address": "Adresse", "notes": "Hinweise", "type": "Typ",
        "drop": "Zustellung", "pickup": "Abholung", "load": "An Bord danach",
        "window": "Zeitfenster", "done": "Erledigt", "depot": "Depot",
        "unplaced": "Nicht eingeplant", "reason": "Grund", "id": "ID",
        "floor": "Stock", "products": "Produkte", "min": "min",
        "footer": "Zeiten sind Schätzungen (Luftlinie × Umwegfaktor). Erstellt am {date}.",
        "page": "Seite {n}", "until": "bis", "from": "ab", "end_at": "Ende",
    },
    "fr": {
        "tour": "Tournée", "vehicle": "Véhicule", "start": "Départ", "end": "Retour",
        "duration": "Durée", "stops": "Arrêts", "items": "Colis", "capacity": "Capacité",
        "nr": "N°", "arrival": "Arrivée", "departure": "Départ", "stop": "Arrêt",
        "name": "Nom", "address": "Adresse", "notes": "Remarques", "type": "Type",
        "drop": "Livraison", "pickup": "Enlèvement", "load": "À bord ensuite",
        "window": "Créneau", "done": "Fait", "depot": "Dépôt",
        "unplaced": "Non planifiés", "reason": "Raison", "id": "ID",
        "floor": "Étage", "products": "Produits", "min": "min",
        "footer": "Horaires estimés (vol d'oiseau × facteur de détour). Créé le {date}.",
        "page": "Page {n}", "until": "jusqu'à", "from": "dès", "end_at": "Fin",
    },
    "en": {
        "tour": "Tour", "vehicle": "Vehicle", "start": "Start", "end": "Back",
        "duration": "Duration", "stops": "Stops", "items": "Items", "capacity": "Capacity",
        "nr": "No.", "arrival": "Arrival", "departure": "Departure", "stop": "Stop",
        "name": "Name", "address": "Address", "notes": "Notes", "type": "Type",
        "drop": "Drop", "pickup": "Pickup", "load": "On board after",
        "window": "Time window", "done": "Done", "depot": "Depot",
        "unplaced": "Not planned", "reason": "Reason", "id": "ID",
        "floor": "Floor", "products": "Products", "min": "min",
        "footer": "Times are estimates (crow-fly × detour factor). Created {date}.",
        "page": "Page {n}", "until": "until", "from": "from", "end_at": "End",
    },
}


def labels(lang: str) -> dict[str, str]:
    return LABELS.get(lang, LABELS["de"])


def _totals(stops) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in stops:
        for name, n in s.quantities:
            out[name] = out.get(name, 0) + n
    return out


def _qty_text(q: dict[str, int]) -> str:
    return ", ".join(f"{n} × {k}" for k, n in q.items() if n)


def _window(t: ScheduledStop, L: dict) -> str:
    s, e = t.stop.tw_start, t.stop.tw_end
    if s is None and e is None:
        return ""
    if s is None:
        return f"{L['until']} {format_time(e)}"
    if e is None:
        return f"{L['from']} {format_time(s)}"
    return f"{format_time(s)}–{format_time(e)}"


def _inner(schedule: Schedule) -> list[ScheduledStop]:
    """The stops between start and end (the depot rows are shown apart)."""
    return list(schedule.times[1:-1])


def _notes(stop, skip_in_name: bool = True) -> list[tuple[str, str]]:
    """Info columns worth printing: without values already in the name."""
    out = []
    for key, value in stop.info:
        value = value.strip()
        if not value:
            continue
        if skip_in_name and stop.name and value in stop.name:
            continue
        out.append((key, value))
    return out


def _sheet_title(name: str, used: set[str]) -> str:
    title = re.sub(r"[\[\]:*?/\\]", "-", name)[:31] or "Tour"
    base, n = title, 2
    while title in used:
        suffix = f" ({n})"
        title = base[:31 - len(suffix)] + suffix
        n += 1
    used.add(title)
    return title


# --------------------------------------------------------------------------
# Excel: one sheet per tour
# --------------------------------------------------------------------------

def tours_xlsx(schedules: Sequence[Schedule], unplaced: Sequence[Unplaced] = (),
               lang: str = "de") -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    L = labels(lang)
    wb = Workbook()
    wb.remove(wb.active)
    used: set[str] = set()
    bold = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="DDDDDD")
    thin = Border(bottom=Side(style="thin", color="999999"))
    wrap = Alignment(wrap_text=True, vertical="top")

    for schedule in schedules:
        tour = schedule.tour
        stops = [t.stop for t in schedule.times]
        products = list(_totals(stops))
        info_keys = list(dict.fromkeys(k for s in stops for k, _ in s.info))
        ws = wb.create_sheet(_sheet_title(tour.tour_id, used))

        # header block
        ws.append([f"{L['tour']} {tour.tour_id}"])
        ws["A1"].font = Font(bold=True, size=14)
        load = max_load(stops)
        facts = [
            (L["vehicle"], tour.vehicle or "–"),
            (L["start"], format_time(schedule.times[0].departure)),
            (L["end"], format_time(schedule.times[-1].arrival)),
            (L["duration"], f"{schedule.duration} {L['min']}"),
            (L["stops"], len(_inner(schedule))),
            (L["items"], f"{load}" + (f" / {tour.capacity}" if tour.capacity is not None else "")),
        ]
        totals = _totals(stops)
        if totals:
            facts.append((L["products"], _qty_text(totals)))
        facts.append((L["depot"], schedule.times[0].stop.address))
        for k, v in facts:
            ws.append([k, v])
            ws.cell(ws.max_row, 1).font = bold
        ws.append([])

        header = [L["nr"], L["arrival"], L["departure"], L["id"], L["name"], L["address"],
                  L["type"], L["items"], *products, L["load"], L["window"], *info_keys, L["done"]]
        ws.append(header)
        hrow = ws.max_row
        for c in range(1, len(header) + 1):
            cell = ws.cell(hrow, c)
            cell.font, cell.fill, cell.alignment = bold, head_fill, wrap
        loads = load_after(stops)
        for number, (t, after) in enumerate(zip(schedule.times[1:-1], loads[1:-1]), start=1):
            s = t.stop
            q, info = dict(s.quantities), dict(s.info)
            ws.append([number, format_time(t.arrival), format_time(t.departure), s.stop_id,
                       s.name, s.address, L["pickup"] if s.kind == PICKUP else L["drop"],
                       s.items if s.items is not None else "",
                       *(q.get(p, 0) for p in products), after, _window(t, L),
                       *(info.get(k, "") for k in info_keys), ""])
            for c in range(1, len(header) + 1):
                ws.cell(ws.max_row, c).alignment = wrap
                ws.cell(ws.max_row, c).border = thin
        end = schedule.times[-1]
        ws.append(["", format_time(end.arrival), "", "", L["end_at"], end.stop.address])

        widths = {L["nr"]: 5, L["arrival"]: 8, L["departure"]: 8, L["id"]: 9, L["name"]: 26,
                  L["address"]: 32, L["type"]: 11, L["items"]: 7, L["load"]: 9,
                  L["window"]: 12, L["done"]: 9}
        for i, h in enumerate(header, start=1):
            ws.column_dimensions[get_column_letter(i)].width = widths.get(h, 16 if h in products else 24)
        ws.column_dimensions["A"].width = max(ws.column_dimensions["A"].width, 12)
        ws.freeze_panes = ws.cell(hrow + 1, 1)
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToHeight = 0

    if unplaced:
        ws = wb.create_sheet(_sheet_title(L["unplaced"], used))
        products = list(_totals(unplaced))
        info_keys = list(dict.fromkeys(k for u in unplaced for k, _ in u.info))
        header = [L["id"], L["name"], L["address"], L["type"], L["items"], *products,
                  L["reason"], *info_keys]
        ws.append(header)
        for c in range(1, len(header) + 1):
            ws.cell(1, c).font, ws.cell(1, c).fill = bold, head_fill
        for u in unplaced:
            q, info = dict(u.quantities), dict(u.info)
            ws.append([u.order_id, u.name, u.address, L["pickup"] if u.kind == PICKUP else L["drop"],
                       u.items if u.items is not None else "", *(q.get(p, 0) for p in products),
                       u.reason, *(info.get(k, "") for k in info_keys)])
        for i in range(1, len(header) + 1):
            ws.column_dimensions[get_column_letter(i)].width = 24

    if not wb.worksheets:
        wb.create_sheet("Tours")
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# --------------------------------------------------------------------------
# PDF: one page (or more) per tour, for the driver
# --------------------------------------------------------------------------

def tours_pdf(schedules: Sequence[Schedule], lang: str = "de") -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (PageBreak, Paragraph,
                                    SimpleDocTemplate, Spacer, Table, TableStyle)

    L = labels(lang)
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=9, leading=11.5)
    small = ParagraphStyle("small", parent=base, fontSize=7.5, leading=9.5,
                           textColor=colors.HexColor("#555555"))
    bold = ParagraphStyle("bold", parent=base, fontName="Helvetica-Bold")
    big = ParagraphStyle("big", parent=base, fontName="Helvetica-Bold", fontSize=13, leading=16)
    title = ParagraphStyle("title", parent=base, fontName="Helvetica-Bold", fontSize=18, leading=22)
    head = ParagraphStyle("head", parent=base, fontName="Helvetica-Bold", fontSize=8,
                          textColor=colors.HexColor("#333333"))

    def esc(text) -> str:
        return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("\n", "<br/>"))

    created = date.today().strftime("%d.%m.%Y")
    story = []
    width = A4[0] - 24 * mm

    for index, schedule in enumerate(schedules):
        tour = schedule.tour
        stops = [t.stop for t in schedule.times]
        inner = _inner(schedule)
        totals = _totals(stops)
        load = max_load(stops)
        if index:
            story.append(PageBreak())
        story.append(Paragraph(esc(f"{L['tour']} {tour.tour_id}"), title))
        facts = [
            (L["vehicle"], tour.vehicle or "–"),
            (L["start"], format_time(schedule.times[0].departure)),
            (L["end"], format_time(schedule.times[-1].arrival)),
            (L["stops"], str(len(inner))),
            (L["items"], f"{load}" + (f" / {tour.capacity}" if tour.capacity is not None else "")),
        ]
        cells = [[Paragraph(esc(k), small) for k, _ in facts],
                 [Paragraph(esc(v), big) for _, v in facts]]
        box = Table(cells, colWidths=[width / len(facts)] * len(facts))
        box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F2F2F2")),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 6),
                                 ("TOPPADDING", (0, 0), (-1, 0), 5),
                                 ("BOTTOMPADDING", (0, -1), (-1, -1), 6)]))
        story += [Spacer(1, 3 * mm), box]
        if totals:
            story += [Spacer(1, 2 * mm),
                      Paragraph(f"<b>{esc(L['products'])}:</b> {esc(_qty_text(totals))}", base)]
        story += [Spacer(1, 2 * mm),
                  Paragraph(f"<b>{esc(L['depot'])}:</b> {esc(schedule.times[0].stop.address)}", base),
                  Spacer(1, 4 * mm)]

        rows = [[Paragraph(esc(L["nr"]), head), Paragraph(esc(L["arrival"]), head),
                 Paragraph(esc(L["stop"]), head), Paragraph(esc(L["items"]), head),
                 Paragraph(esc(L["notes"]), head), Paragraph(esc(L["done"]), head)]]
        for number, t in enumerate(inner, start=1):
            s = t.stop
            who = [f"<b>{esc(s.name)}</b>" if s.name else f"<b>{esc(s.stop_id)}</b>",
                   esc(s.address)]
            floor = dict(s.info).get("floor")
            if floor:
                who.append(f"<b>{esc(L['floor'])}: {esc(floor)}</b>")
            if s.name:
                who.append(f"<font color='#777777'>{esc(L['id'])} {esc(s.stop_id)}</font>")
            q = dict(s.quantities)
            items = [f"<b>{s.items if s.items is not None else ''}</b>"]
            if s.kind == PICKUP:
                items.append(esc(L["pickup"]))
            items += [esc(f"{n} × {k}") for k, n in q.items() if n]
            notes = []
            window = _window(t, L)
            if window:
                notes.append(f"<b>{esc(L['window'])}: {esc(window)}</b>")
            notes += [f"<font color='#555555'>{esc(k)}:</font> {esc(v)}"
                      for k, v in _notes(s) if k != "floor"]
            rows.append([Paragraph(str(number), bold),
                         Paragraph(f"<b>{format_time(t.arrival)}</b>", base),
                         Paragraph("<br/>".join(who), base),
                         Paragraph("<br/>".join(items), base),
                         Paragraph("<br/>".join(notes), small),
                         ""])
        end = schedule.times[-1]
        rows.append([Paragraph("", base), Paragraph(f"<b>{format_time(end.arrival)}</b>", base),
                     Paragraph(f"<b>{esc(L['end_at'])}:</b> {esc(end.stop.address)}", base),
                     "", "", ""])
        cols = [9 * mm, 15 * mm, 62 * mm, 26 * mm, None, 14 * mm]
        cols[4] = width - sum(c for c in cols if c)
        table = Table(rows, colWidths=cols, repeatRows=1)
        style = [("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black),
                 ("LINEBELOW", (0, 1), (-1, -2), 0.3, colors.HexColor("#BBBBBB")),
                 ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                 ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
        for r in range(1, len(rows) - 1):          # empty tick box per stop
            style.append(("BOX", (5, r), (5, r), 0.6, colors.HexColor("#888888")))
            if r % 2 == 0:
                style.append(("BACKGROUND", (0, r), (4, r), colors.HexColor("#F7F7F7")))
        table.setStyle(TableStyle(style))
        story.append(table)

    if not story:
        story.append(Paragraph("–", base))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawString(12 * mm, 8 * mm, L["footer"].format(date=created))
        canvas.drawRightString(A4[0] - 12 * mm, 8 * mm, L["page"].format(n=doc.page))
        canvas.restoreState()

    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=14 * mm,
                            title="Tours", author="tour-insertion-tool")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()
