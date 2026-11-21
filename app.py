"""Web app for the tour insertion tool (Streamlit).

Run locally:   uv sync --extra app && uv run streamlit run app.py
Same code runs on Streamlit Community Cloud (entry point: app.py).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Make `tour_insertion` importable without installing the package
# (Streamlit Community Cloud only installs requirements.txt).
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import streamlit as st
import streamlit.components.v1 as components

from tour_insertion.csv_io import (format_time, geocoding_to_csv, read_rows,
                                   read_rows_text, schedules_to_csv,
                                   skipped_to_csv, unplaced_to_csv)
from tour_insertion.mapview import map_html
from tour_insertion.pipeline import InputError, Settings, make_geocoder, run

ROOT = Path(__file__).resolve().parent
EXAMPLES = ROOT / "examples"
PROFILES = json.loads((ROOT / "profiles.json").read_text(encoding="utf-8"))
PROFILES.pop("_comment", None)

LANGUAGES = {"Deutsch": "de", "Français": "fr", "English": "en"}

TEXT = {
    "title": {"de": "Aufträge in Touren einfügen",
              "fr": "Insérer des commandes dans les tournées",
              "en": "Insert orders into tours"},
    "intro": {"de": "Lade die geplanten Touren und die neuen Aufträge hoch. Das Tool setzt "
                    "jeden neuen Stopp dort ein, wo er am wenigsten Umweg kostet, und hält "
                    "alle Zeitfenster ein. Bestehende Touren werden nicht umgestellt.",
              "fr": "Chargez les tournées planifiées et les nouvelles commandes. L'outil place "
                    "chaque nouvel arrêt là où il coûte le moins de détour, en respectant "
                    "les créneaux horaires. L'ordre des tournées existantes n'est pas modifié.",
              "en": "Upload the planned tours and the new orders. The tool puts each new stop "
                    "where it costs the least detour and keeps all time windows. Existing "
                    "tours are not rearranged."},
    "settings": {"de": "Einstellungen", "fr": "Paramètres", "en": "Settings"},
    "language": {"de": "Sprache", "fr": "Langue", "en": "Language"},
    "profile": {"de": "Profil", "fr": "Profil", "en": "Profile"},
    "speed": {"de": "Tempo (km/h, Annahme)", "fr": "Vitesse (km/h, hypothèse)",
              "en": "Speed (km/h, assumption)"},
    "detour": {"de": "Umwegfaktor (Strasse / Luftlinie)",
               "fr": "Facteur de détour (route / vol d'oiseau)",
               "en": "Detour factor (street / crow-fly)"},
    "service": {"de": "Standzeit pro Stopp (min)", "fr": "Temps d'arrêt par livraison (min)",
                "en": "Service time per stop (min)"},
    "tolerance": {"de": "Toleranz Zeitfenster (min)", "fr": "Tolérance créneau (min)",
                  "en": "Time window tolerance (min)"},
    "advanced": {"de": "Erweitert", "fr": "Avancé", "en": "Advanced"},
    "open_start": {"de": "Vor dem ersten Stopp einfügen erlauben",
                   "fr": "Autoriser avant le premier arrêt",
                   "en": "Allow before the first stop"},
    "open_end": {"de": "Nach dem letzten Stopp einfügen erlauben",
                 "fr": "Autoriser après le dernier arrêt",
                 "en": "Allow after the last stop"},
    "min_score": {"de": "Mindest-Score Geocoding (FR)", "fr": "Score minimal géocodage (FR)",
                  "en": "Minimum geocoding score (FR)"},
    "demo_note": {"de": "Demo-Version: bitte nur erfundene oder anonymisierte Daten hochladen.",
                  "fr": "Version de démonstration : n'utilisez que des données fictives ou anonymisées.",
                  "en": "Demo version: please upload invented or anonymised data only."},
    "source": {"de": "Daten", "fr": "Données", "en": "Data"},
    "upload": {"de": "Eigene Dateien hochladen", "fr": "Charger mes fichiers",
               "en": "Upload my files"},
    "example_w": {"de": "Beispiel Winterthur (mit Koordinaten)",
                  "fr": "Exemple Winterthour (avec coordonnées)",
                  "en": "Example Winterthur (with coordinates)"},
    "example_l": {"de": "Beispiel Lyon (nur Adressen)", "fr": "Exemple Lyon (adresses seules)",
                  "en": "Example Lyon (addresses only)"},
    "tours_file": {"de": "1. Geplante Touren (CSV)", "fr": "1. Tournées planifiées (CSV)",
                   "en": "1. Planned tours (CSV)"},
    "orders_file": {"de": "2. Neue Aufträge (CSV)", "fr": "2. Nouvelles commandes (CSV)",
                    "en": "2. New orders (CSV)"},
    "templates": {"de": "Vorlagen herunterladen", "fr": "Télécharger les modèles",
                  "en": "Download templates"},
    "template_tours": {"de": "Vorlage Touren", "fr": "Modèle tournées", "en": "Tours template"},
    "template_orders": {"de": "Vorlage Aufträge", "fr": "Modèle commandes",
                        "en": "Orders template"},
    "run": {"de": "Einfügen", "fr": "Insérer", "en": "Insert"},
    "running": {"de": "Rechne … (Adressen werden beim ersten Mal nachgeschlagen)",
                "fr": "Calcul … (les adresses sont géocodées la première fois)",
                "en": "Computing … (addresses are geocoded the first time)"},
    "need_files": {"de": "Bitte beide Dateien hochladen.",
                   "fr": "Veuillez charger les deux fichiers.",
                   "en": "Please upload both files."},
    "error": {"de": "Die Daten konnten nicht verarbeitet werden:",
              "fr": "Les données n'ont pas pu être traitées :",
              "en": "The data could not be processed:"},
    "m_tours": {"de": "Touren", "fr": "Tournées", "en": "Tours"},
    "m_orders": {"de": "Neue Aufträge", "fr": "Nouvelles commandes", "en": "New orders"},
    "m_placed": {"de": "Eingefügt", "fr": "Insérées", "en": "Placed"},
    "m_unplaced": {"de": "Nicht einplanbar", "fr": "Non planifiables", "en": "Not placed"},
    "map": {"de": "Karte", "fr": "Carte", "en": "Map"},
    "inserted": {"de": "Eingefügte Aufträge", "fr": "Commandes insérées",
                 "en": "Inserted orders"},
    "unplaced": {"de": "Nicht einplanbare Aufträge", "fr": "Commandes non planifiables",
                 "en": "Orders not placed"},
    "all_tours": {"de": "Alle Touren mit Zeiten", "fr": "Toutes les tournées avec horaires",
                  "en": "All tours with times"},
    "geocoding": {"de": "Geocoding prüfen", "fr": "Vérifier le géocodage",
                  "en": "Check geocoding"},
    "downloads": {"de": "Herunterladen", "fr": "Télécharger", "en": "Download"},
    "assumptions": {"de": "Zeiten sind Schätzungen: Luftlinie × Umwegfaktor bei konstantem Tempo. "
                          "Bitte vor dem Versand an Fahrer:innen prüfen.",
                    "fr": "Les horaires sont des estimations : distance à vol d'oiseau × facteur "
                          "de détour à vitesse constante. À vérifier avant l'envoi aux livreurs.",
                    "en": "Times are estimates: crow-fly distance × detour factor at constant "
                          "speed. Check before sending to drivers."},
    "col_order": {"de": "Auftrag", "fr": "Commande", "en": "Order"},
    "col_tour": {"de": "Tour", "fr": "Tournée", "en": "Tour"},
    "col_pos": {"de": "Stopp Nr.", "fr": "Arrêt n°", "en": "Stop no."},
    "col_extra": {"de": "Umweg (min)", "fr": "Détour (min)", "en": "Detour (min)"},
    "col_arrival": {"de": "Ankunft", "fr": "Arrivée", "en": "Arrival"},
    "col_address": {"de": "Adresse", "fr": "Adresse", "en": "Address"},
    "col_reason": {"de": "Grund", "fr": "Raison", "en": "Reason"},
    "no_feasible_position": {"de": "passt in keine Tour (Zeitfenster)",
                             "fr": "n'entre dans aucune tournée (créneaux)",
                             "en": "fits no tour (time windows)"},
    "geocode_not_found": {"de": "Adresse nicht gefunden", "fr": "adresse introuvable",
                          "en": "address not found"},
    "geocode_uncertain": {"de": "Adresse unsicher, bitte prüfen",
                          "fr": "adresse incertaine, à vérifier",
                          "en": "address uncertain, please check"},
    "optimize": {"de": "Touren optimieren (Reihenfolge innerhalb jeder Tour)",
                 "fr": "Optimiser les tournées (ordre dans chaque tournée)",
                 "en": "Optimise tours (order within each tour)"},
    "optimize_help": {"de": "Stellt auch die bestehenden Stopps um, wenn die Tour dadurch kürzer "
                            "wird. Zeitfenster bleiben eingehalten, keine Stopps wechseln die Tour.",
                      "fr": "Réordonne aussi les arrêts existants si la tournée devient plus courte. "
                            "Les créneaux sont respectés, aucun arrêt ne change de tournée.",
                      "en": "Also reorders existing stops if the tour gets shorter. Time windows "
                            "are kept, no stop changes tour."},
    "skipped_warn": {"de": "{n} Stopp(s) bestehender Touren wurden nicht gefunden und ohne sie "
                           "gerechnet. Die Zeiten dieser Touren sind dadurch etwas zu früh.",
                     "fr": "{n} arrêt(s) des tournées existantes introuvable(s), calcul effectué "
                           "sans eux. Les horaires de ces tournées sont donc un peu trop tôt.",
                     "en": "{n} stop(s) of existing tours not found and left out. Times of those "
                           "tours are therefore slightly too early."},
    "skipped": {"de": "Übersprungene Tour-Stopps (Adresse nicht gefunden)",
                "fr": "Arrêts ignorés (adresse introuvable)",
                "en": "Skipped tour stops (address not found)"},
    "optimization": {"de": "Optimierung", "fr": "Optimisation", "en": "Optimisation"},
    "opt_summary": {"de": "Gesamt {before} → {after} min, {saved} min gespart, "
                          "{moved} Stopps in anderer Reihenfolge.",
                    "fr": "Total {before} → {after} min, {saved} min gagnées, "
                          "{moved} arrêts dans un autre ordre.",
                    "en": "Total {before} → {after} min, {saved} min saved, "
                          "{moved} stops in a different order."},
    "opt_note": {"de": "Vorher = nur eingefügt, bestehende Reihenfolge unverändert.",
                 "fr": "Avant = insertion seule, ordre existant inchangé.",
                 "en": "Before = insertion only, existing order unchanged."},
    "col_before": {"de": "Vorher (min)", "fr": "Avant (min)", "en": "Before (min)"},
    "col_after": {"de": "Nachher (min)", "fr": "Après (min)", "en": "After (min)"},
    "col_saved": {"de": "Gespart (min)", "fr": "Gagné (min)", "en": "Saved (min)"},
    "col_moved": {"de": "Umgestellte Stopps", "fr": "Arrêts déplacés", "en": "Moved stops"},
    "col_stop": {"de": "Stopp", "fr": "Arrêt", "en": "Stop"},
    "no_tours": {"de": "keine Touren vorhanden", "fr": "aucune tournée", "en": "no tours"},
}


def t(key: str) -> str:
    return TEXT[key][st.session_state.get("lang", "de")]


@st.cache_resource
def shared_geocoder(min_score: float):
    """One geocoder per score threshold, cache shared by all sessions
    (it only holds public address data)."""
    cache = Path(tempfile.gettempdir()) / "tour_insertion_geocode_cache.json"
    return make_geocoder(Settings(min_score=min_score), cache)


def sidebar() -> Settings:
    with st.sidebar:
        language = st.selectbox("Sprache · Langue · Language", list(LANGUAGES))
        st.session_state.lang = LANGUAGES[language]
        st.header(t("settings"))
        name = st.selectbox(t("profile"), list(PROFILES))
        p = PROFILES[name]
        speed_kmh = st.number_input(t("speed"), 5.0, 40.0,
                                    p["speed_m_per_min"] * 60 / 1000, 0.5,
                                    key=f"speed_{name}")
        detour = st.number_input(t("detour"), 1.0, 2.5, float(p["detour_factor"]),
                                 0.05, key=f"detour_{name}")
        service = st.number_input(t("service"), 0, 60, int(p["service_min"]), 1,
                                  key=f"service_{name}")
        tolerance = st.number_input(t("tolerance"), 0, 120, int(p["tolerance_min"]),
                                    1, key=f"tol_{name}")
        optimize = st.checkbox(t("optimize"), bool(p.get("optimize", False)),
                               help=t("optimize_help"), key=f"opt_{name}")
        with st.expander(t("advanced")):
            open_start = st.checkbox(t("open_start"), p["open_start"], key=f"os_{name}")
            open_end = st.checkbox(t("open_end"), p["open_end"], key=f"oe_{name}")
            min_score = st.slider(t("min_score"), 0.0, 1.0, float(p["min_score"]), 0.05,
                                  key=f"ms_{name}")
        st.info(t("demo_note"))
    return Settings(detour_factor=detour, speed_m_per_min=speed_kmh * 1000 / 60,
                    service_min=service, tolerance_min=tolerance,
                    open_start=open_start, open_end=open_end, min_score=min_score,
                    optimize=optimize)


def load_rows():
    """(tour_rows, order_rows) from the chosen source, or None."""
    options = [t("upload"), t("example_w"), t("example_l")]
    source = st.radio(t("source"), options, horizontal=True)
    if source == options[1]:
        folder = EXAMPLES / "winterthur"
    elif source == options[2]:
        folder = EXAMPLES / "lyon"
    else:
        folder = None

    if folder is not None:
        st.caption(f"`examples/{folder.name}/tours.csv` · `examples/{folder.name}/orders.csv`")
        return lambda: (read_rows(folder / "tours.csv"), read_rows(folder / "orders.csv"))

    left, right = st.columns(2)
    tours_file = left.file_uploader(t("tours_file"), type="csv")
    orders_file = right.file_uploader(t("orders_file"), type="csv")
    with st.expander(t("templates")):
        c1, c2 = st.columns(2)
        c1.download_button(t("template_tours"),
                           (EXAMPLES / "lyon" / "tours.csv").read_bytes(),
                           "tours_template.csv", "text/csv")
        c2.download_button(t("template_orders"),
                           (EXAMPLES / "lyon" / "orders.csv").read_bytes(),
                           "orders_template.csv", "text/csv")
    if tours_file is None or orders_file is None:
        return None
    return lambda: (read_rows_text(tours_file.getvalue().decode("utf-8-sig"), tours_file.name),
                    read_rows_text(orders_file.getvalue().decode("utf-8-sig"), orders_file.name))


def show_result(result) -> None:
    c = st.columns(4)
    c[0].metric(t("m_tours"), len(result.tours))
    c[1].metric(t("m_orders"), result.n_orders)
    c[2].metric(t("m_placed"), len(result.inserted))
    c[3].metric(t("m_unplaced"), len(result.unplaced))

    if result.skipped:
        st.warning(t("skipped_warn").format(n=len(result.skipped)))

    title = f"{len(result.inserted)} / {result.n_orders}"
    st.subheader(t("map"))
    html = map_html(result.schedules, result.unplaced, title=title, moved=result.moved)
    components.html(html, height=620)
    st.caption(t("assumptions"))

    if result.changes:
        st.subheader(t("optimization"))
        before = sum(c.minutes_before for c in result.changes)
        after = sum(c.minutes_after for c in result.changes)
        st.write(t("opt_summary").format(before=before, after=after, saved=before - after,
                                         moved=len(result.moved)))
        st.dataframe([{t("col_tour"): c.tour_id, t("col_before"): c.minutes_before,
                       t("col_after"): c.minutes_after, t("col_saved"): c.saved,
                       t("col_moved"): len(c.moved)} for c in result.changes],
                     hide_index=True, width="stretch")
        st.caption(t("opt_note"))

    # final position (after optimizing), not the one at insertion time
    where = {tm.stop.stop_id: (s.tour.tour_id, i, tm.arrival)
             for s in result.schedules for i, tm in enumerate(s.times)}
    left, right = st.columns(2)
    with left:
        st.subheader(t("inserted"))
        rows = []
        for o in result.inserted:
            tour_id, pos, arrival = where[o.stop_id]
            rows.append({t("col_order"): o.stop_id, t("col_tour"): tour_id,
                         t("col_pos"): pos, t("col_extra"): round(o.delta, 1),
                         t("col_arrival"): format_time(arrival)})
        rows.sort(key=lambda r: (r[t("col_tour")], r[t("col_pos")]))
        st.dataframe(rows, hide_index=True, width="stretch")
    with right:
        st.subheader(t("unplaced"))
        st.dataframe([{t("col_order"): u.order_id, t("col_address"): u.address,
                       t("col_reason"): TEXT.get(u.reason, {}).get(st.session_state.lang,
                                                                    u.reason)}
                      for u in result.unplaced],
                     hide_index=True, width="stretch")

    if result.skipped:
        st.subheader(t("skipped"))
        st.dataframe([{t("col_tour"): u.tour_id, t("col_stop"): u.order_id,
                       t("col_address"): u.address,
                       t("col_reason"): TEXT.get(u.reason, {}).get(st.session_state.lang,
                                                                    u.reason)}
                      for u in result.skipped],
                     hide_index=True, width="stretch")

    with st.expander(t("all_tours")):
        st.dataframe([{"tour": s.tour.tour_id, "#": i, "stop": tm.stop.stop_id,
                       "new": "●" if tm.stop.is_new else "",
                       "moved": "↻" if tm.stop.stop_id in result.moved else "",
                       t("col_address"): tm.stop.address,
                       t("col_arrival"): format_time(tm.arrival),
                       "tw": f"{format_time(tm.stop.tw_start)}–{format_time(tm.stop.tw_end)}"
                       if tm.stop.tw_start is not None or tm.stop.tw_end is not None else "",
                       "slack": tm.slack}
                      for s in result.schedules for i, tm in enumerate(s.times, start=1)],
                     hide_index=True, width="stretch")
    if result.geocoding:
        with st.expander(t("geocoding")):
            st.dataframe([{"address": g.query, "country": g.country, "status": g.status,
                           "score": g.score, "found": g.label}
                          for g in result.geocoding],
                         hide_index=True, width="stretch")

    st.subheader(t("downloads"))
    d = st.columns(4)
    d[0].download_button("tours_new.csv", schedules_to_csv(result.schedules, result.moved),
                         "tours_new.csv", "text/csv")
    d[1].download_button("unplaced.csv", unplaced_to_csv(result.unplaced),
                         "unplaced.csv", "text/csv")
    d[2].download_button("map.html", html, "map.html", "text/html")
    if result.geocoding:
        d[3].download_button("geocoding.csv", geocoding_to_csv(result.geocoding),
                             "geocoding.csv", "text/csv")
    if result.skipped:
        st.download_button("skipped_stops.csv", skipped_to_csv(result.skipped),
                           "skipped_stops.csv", "text/csv")


def main() -> None:
    st.set_page_config(page_title="Tour Insertion Tool", page_icon="🚲", layout="wide")
    settings = sidebar()
    st.title(t("title"))
    st.write(t("intro"))

    loader = load_rows()
    if st.button(t("run"), type="primary"):
        if loader is None:
            st.warning(t("need_files"))
        else:
            try:
                with st.spinner(t("running")):
                    tour_rows, order_rows = loader()
                    st.session_state.result = run(
                        tour_rows, order_rows, settings,
                        shared_geocoder(settings.min_score))
            except (InputError, UnicodeDecodeError) as error:
                st.session_state.pop("result", None)
                st.error(t("error"))
                st.code(str(error), language=None)

    if "result" in st.session_state:
        show_result(st.session_state.result)


main()
