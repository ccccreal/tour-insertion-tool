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
                                   unplaced_to_csv)
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
        with st.expander(t("advanced")):
            open_start = st.checkbox(t("open_start"), p["open_start"], key=f"os_{name}")
            open_end = st.checkbox(t("open_end"), p["open_end"], key=f"oe_{name}")
            min_score = st.slider(t("min_score"), 0.0, 1.0, float(p["min_score"]), 0.05,
                                  key=f"ms_{name}")
        st.info(t("demo_note"))
    return Settings(detour_factor=detour, speed_m_per_min=speed_kmh * 1000 / 60,
                    service_min=service, tolerance_min=tolerance,
                    open_start=open_start, open_end=open_end, min_score=min_score)


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

    title = f"{len(result.inserted)} / {result.n_orders}"
    st.subheader(t("map"))
    html = map_html(result.schedules, result.unplaced, title=title)
    components.html(html, height=620)
    st.caption(t("assumptions"))

    arrival = {tm.stop.stop_id: tm.arrival for s in result.schedules for tm in s.times}
    left, right = st.columns(2)
    with left:
        st.subheader(t("inserted"))
        st.dataframe([{t("col_order"): o.stop_id, t("col_tour"): o.tour_id,
                       t("col_pos"): o.position, t("col_extra"): o.delta,
                       t("col_arrival"): format_time(arrival.get(o.stop_id))}
                      for o in sorted(result.inserted, key=lambda o: (o.tour_id, o.position))],
                     hide_index=True, width="stretch")
    with right:
        st.subheader(t("unplaced"))
        st.dataframe([{t("col_order"): u.order_id, t("col_address"): u.address,
                       t("col_reason"): TEXT.get(u.reason, {}).get(st.session_state.lang,
                                                                    u.reason)}
                      for u in result.unplaced],
                     hide_index=True, width="stretch")

    with st.expander(t("all_tours")):
        st.dataframe([{"tour": s.tour.tour_id, "#": i, "stop": tm.stop.stop_id,
                       "new": "●" if tm.stop.is_new else "",
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
    d[0].download_button("tours_new.csv", schedules_to_csv(result.schedules),
                         "tours_new.csv", "text/csv")
    d[1].download_button("unplaced.csv", unplaced_to_csv(result.unplaced),
                         "unplaced.csv", "text/csv")
    d[2].download_button("map.html", html, "map.html", "text/html")
    if result.geocoding:
        d[3].download_button("geocoding.csv", geocoding_to_csv(result.geocoding),
                             "geocoding.csv", "text/csv")


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
