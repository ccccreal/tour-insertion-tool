"""Map of the tours as a self-contained HTML file (folium / Leaflet).

- every tour: a coloured line in driving order, stops numbered 1, 2, 3 ...
  (the depot is not counted)
- new stops: larger, with a black ring, so they stand out
- first/last stop (depot): square marked D
- orders that fit nowhere: red cross (if they have coordinates)
- click a stop: address, planned arrival, time window, slack
"""

from __future__ import annotations

import html
from pathlib import Path

import folium

from tour_insertion.csv_io import format_time
from tour_insertion.model import Schedule
from tour_insertion.planner import Unplaced

# Colour-blind-friendly palette (Okabe-Ito), no red: red means "not placed"
COLOURS = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9",
           "#F0E442", "#000000", "#999999"]
UNPLACED_COLOUR = "#D7263D"


def _popup(tour_id: str, seq: int, t) -> str:
    s = t.stop
    window = ""
    if s.tw_start is not None or s.tw_end is not None:
        window = (f"<br>window {format_time(s.tw_start) or '…'}"
                  f"–{format_time(s.tw_end) or '…'}")
    slack = f"<br>slack {t.slack} min" if t.slack is not None else ""
    new = " <b>(NEW)</b>" if s.is_new else ""
    return (f"<b>{html.escape(s.stop_id)}</b>{new}<br>"
            f"{html.escape(s.address)}<br>"
            f"tour {html.escape(tour_id)}, stop {seq}<br>"
            f"arrival {format_time(t.arrival)}{window}{slack}")


def _icon(text: str, colour: str, is_new: bool, is_depot: bool) -> folium.DivIcon:
    size = 26 if is_new else 20
    radius = "3px" if is_depot else "50%"
    border = "3px solid #000" if is_new else "1px solid #fff"
    style = (f"width:{size}px;height:{size}px;line-height:{size}px;"
             f"border-radius:{radius};background:{colour};border:{border};"
             "color:#fff;font:bold 11px sans-serif;text-align:center;"
             "box-shadow:0 0 2px rgba(0,0,0,.6);")
    return folium.DivIcon(html=f'<div style="{style}">{text}</div>',
                          icon_size=(size, size),
                          icon_anchor=(size // 2, size // 2))


def build_map(schedules: list[Schedule], unplaced: list[Unplaced] = (),
              title: str = "Tours") -> folium.Map:
    points = [t.stop.point for s in schedules for t in s.times]
    points += [u.stop.point for u in unplaced if u.stop is not None]
    if not points:
        raise ValueError("nothing to draw: no stops with coordinates")

    m = folium.Map(tiles="OpenStreetMap", control_scale=True)
    for i, schedule in enumerate(schedules):
        colour = COLOURS[i % len(COLOURS)]
        tour_id = schedule.tour.tour_id
        layer = folium.FeatureGroup(name=f"{tour_id} ({len(schedule.times)} stops)")
        folium.PolyLine([t.stop.point for t in schedule.times], color=colour,
                        weight=4, opacity=0.8, tooltip=tour_id).add_to(layer)
        last = len(schedule.times)
        number = 0
        for seq, t in enumerate(schedule.times, start=1):
            is_depot = seq in (1, last) and not t.stop.is_new
            if not is_depot:
                number += 1
            folium.Marker(
                t.stop.point,
                icon=_icon("D" if is_depot else str(number), colour,
                           t.stop.is_new, is_depot),
                popup=folium.Popup(_popup(tour_id, seq, t), max_width=260),
                tooltip=f"{tour_id} #{seq}: {t.stop.stop_id}",
            ).add_to(layer)
        layer.add_to(m)

    placed_nowhere = [u for u in unplaced if u.stop is not None]
    if placed_nowhere:
        layer = folium.FeatureGroup(name=f"not placed ({len(placed_nowhere)})")
        for u in placed_nowhere:
            folium.Marker(
                u.stop.point,
                icon=folium.DivIcon(
                    html=f'<div style="color:{UNPLACED_COLOUR};font:bold 26px '
                         'sans-serif;line-height:26px;text-align:center;'
                         'text-shadow:0 0 3px #fff;">✕</div>',
                    icon_size=(26, 26), icon_anchor=(13, 13)),
                popup=folium.Popup(
                    f"<b>{html.escape(u.order_id)}</b> NOT PLACED<br>"
                    f"{html.escape(u.address)}<br>reason: {u.reason}",
                    max_width=260),
                tooltip=f"{u.order_id}: not placed",
            ).add_to(layer)
        layer.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    m.get_root().html.add_child(folium.Element(_legend(title)))
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]],
                 padding=(30, 30))
    return m


def _legend(title: str) -> str:
    return (
        '<div style="position:fixed;bottom:20px;left:20px;z-index:9999;'
        'background:#fff;padding:8px 10px;border-radius:6px;'
        'box-shadow:0 1px 4px rgba(0,0,0,.3);font:12px sans-serif;">'
        f"<b>{html.escape(title)}</b><br>"
        "numbers = driving order, D = depot<br>"
        "black ring = new stop<br>"
                f'<span style="color:{UNPLACED_COLOUR};font-weight:bold">✕</span>'
        " = not placed<br>"
        '<span style="color:#666">times are estimates (crow-fly model)</span>'
        "</div>")


def write_map(path: str | Path, schedules: list[Schedule],
              unplaced: list[Unplaced] = (), title: str = "Tours") -> None:
    build_map(schedules, unplaced, title).save(str(path))
