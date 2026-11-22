// Texts of the web page in German, French and English.
// t(key, {name: value}) replaces {name} in the text.

const TEXT = {
  title: { de: "Aufträge in Touren einfügen", fr: "Insérer des commandes dans les tournées", en: "Insert orders into tours" },
  intro: {
    de: "Lade die geplanten Touren und die neuen Aufträge hoch. Das Tool setzt jeden neuen Stopp dort ein, wo er am wenigsten Umweg kostet, und hält alle Zeitfenster ein. Danach kannst du Stopps auf der Karte mit der Maus in eine andere Tour ziehen.",
    fr: "Chargez les tournées planifiées et les nouvelles commandes. L'outil place chaque nouvel arrêt là où il coûte le moins de détour, en respectant les créneaux. Ensuite, vous pouvez glisser un arrêt sur la carte vers une autre tournée.",
    en: "Upload the planned tours and the new orders. The tool puts each new stop where it costs the least detour and keeps all time windows. Afterwards you can drag stops on the map into another tour."
  },
  data: { de: "Daten", fr: "Données", en: "Data" },
  source_upload: { de: "Eigene Dateien hochladen", fr: "Charger mes fichiers", en: "Upload my files" },
  source_example: { de: "Beispiel verwenden", fr: "Utiliser un exemple", en: "Use an example" },
  ex_winterthur: { de: "Winterthur (klein, mit Koordinaten)", fr: "Winterthour (petit, avec coordonnées)", en: "Winterthur (small, with coordinates)" },
  ex_zuerich: { de: "Zürich (10 Touren, 30 Aufträge)", fr: "Zurich (10 tournées, 30 commandes)", en: "Zurich (10 tours, 30 orders)" },
  ex_lyon: { de: "Lyon (2 Touren, 10 Aufträge)", fr: "Lyon (2 tournées, 10 commandes)", en: "Lyon (2 tours, 10 orders)" },
  ex_paris: { de: "Paris (10 Touren, 30 Aufträge)", fr: "Paris (10 tournées, 30 commandes)", en: "Paris (10 tours, 30 orders)" },
  tours_file: { de: "1. Geplante Touren (CSV)", fr: "1. Tournées planifiées (CSV)", en: "1. Planned tours (CSV)" },
  orders_file: { de: "2. Neue Aufträge (CSV)", fr: "2. Nouvelles commandes (CSV)", en: "2. New orders (CSV)" },
  templates: { de: "Vorlagen", fr: "Modèles", en: "Templates" },
  template_tours: { de: "Touren", fr: "tournées", en: "tours" },
  template_orders: { de: "Aufträge", fr: "commandes", en: "orders" },
  run: { de: "Einfügen", fr: "Insérer", en: "Insert" },
  running: { de: "Rechne …", fr: "Calcul …", en: "Computing …" },
  need_files: { de: "Bitte beide Dateien hochladen.", fr: "Veuillez charger les deux fichiers.", en: "Please upload both files." },
  error: { de: "Die Daten konnten nicht verarbeitet werden:", fr: "Les données n'ont pas pu être traitées :", en: "The data could not be processed:" },
  settings: { de: "Einstellungen", fr: "Paramètres", en: "Settings" },
  profile: { de: "Profil", fr: "Profil", en: "Profile" },
  speed: { de: "Tempo (km/h, Annahme)", fr: "Vitesse (km/h, hypothèse)", en: "Speed (km/h, assumption)" },
  detour: { de: "Umwegfaktor (Strasse / Luftlinie)", fr: "Facteur de détour (route / vol d'oiseau)", en: "Detour factor (street / crow-fly)" },
  service: { de: "Standzeit pro Stopp (min)", fr: "Temps d'arrêt (min)", en: "Service time per stop (min)" },
  tolerance: { de: "Toleranz Zeitfenster (min)", fr: "Tolérance créneau (min)", en: "Time window tolerance (min)" },
  optimize: { de: "Touren optimieren (Reihenfolge innerhalb jeder Tour)", fr: "Optimiser les tournées (ordre dans chaque tournée)", en: "Optimise tours (order within each tour)" },
  optimize_help: {
    de: "Stellt auch bestehende Stopps um, wenn die Tour dadurch kürzer wird. Zeitfenster bleiben eingehalten, kein Stopp wechselt die Tour.",
    fr: "Réordonne aussi les arrêts existants si la tournée devient plus courte. Les créneaux sont respectés, aucun arrêt ne change de tournée.",
    en: "Also reorders existing stops if the tour gets shorter. Time windows are kept, no stop changes tour."
  },
  advanced: { de: "Erweitert", fr: "Avancé", en: "Advanced" },
  open_start: { de: "Vor dem ersten Stopp einfügen erlauben", fr: "Autoriser avant le premier arrêt", en: "Allow before the first stop" },
  open_end: { de: "Nach dem letzten Stopp einfügen erlauben", fr: "Autoriser après le dernier arrêt", en: "Allow after the last stop" },
  min_score: { de: "Mindest-Score Geocoding (FR)", fr: "Score minimal géocodage (FR)", en: "Minimum geocoding score (FR)" },
  demo_note: {
    de: "Zeiten sind Schätzungen. Bitte nur erfundene oder anonymisierte Daten hochladen, solange keine Vereinbarung besteht.",
    fr: "Les horaires sont des estimations. N'utilisez que des données fictives ou anonymisées tant qu'aucun accord n'existe.",
    en: "Times are estimates. Please upload invented or anonymised data only until there is an agreement."
  },
  m_tours: { de: "Touren", fr: "Tournées", en: "Tours" },
  m_orders: { de: "Neue Aufträge", fr: "Nouvelles commandes", en: "New orders" },
  m_placed: { de: "Eingefügt", fr: "Insérées", en: "Placed" },
  m_unplaced: { de: "Nicht einplanbar", fr: "Non planifiables", en: "Not placed" },
  m_late: { de: "Verspätete Stopps", fr: "Arrêts en retard", en: "Late stops" },
  map: { de: "Karte", fr: "Carte", en: "Map" },
  drag_hint: { de: "Stopp auf die Linie einer Tour ziehen, um ihn dort einzufügen.", fr: "Glissez un arrêt sur la ligne d'une tournée pour l'y insérer.", en: "Drag a stop onto a tour's line to insert it there." },
  undo: { de: "Rückgängig", fr: "Annuler", en: "Undo" },
  legend_title: { de: "Touren", fr: "Tournées", en: "Tours" },
  skipped_warn: {
    de: "{n} Stopp(s) bestehender Touren wurden nicht gefunden und ohne sie gerechnet. Die Zeiten dieser Touren sind dadurch etwas zu früh.",
    fr: "{n} arrêt(s) des tournées existantes introuvable(s), calcul effectué sans eux. Les horaires de ces tournées sont donc un peu trop tôt.",
    en: "{n} stop(s) of existing tours not found and left out. Times of those tours are therefore slightly too early."
  },
  late_warn: {
    de: "{n} Stopp(s) liegen nach dem Ende ihres Zeitfensters (rot markiert), wahrscheinlich nach einer manuellen Verschiebung.",
    fr: "{n} arrêt(s) après la fin de leur créneau (en rouge), probablement après un déplacement manuel.",
    en: "{n} stop(s) are after the end of their time window (marked red), probably after a manual move."
  },
  moved_toast: { de: "{stop}: {from} → {to}. {deltas}", fr: "{stop} : {from} → {to}. {deltas}", en: "{stop}: {from} → {to}. {deltas}" },
  optimization: { de: "Optimierung", fr: "Optimisation", en: "Optimisation" },
  opt_summary: {
    de: "Gesamt {before} → {after} min, {saved} min gespart, {moved} Stopps in anderer Reihenfolge.",
    fr: "Total {before} → {after} min, {saved} min gagnées, {moved} arrêts dans un autre ordre.",
    en: "Total {before} → {after} min, {saved} min saved, {moved} stops in a different order."
  },
  opt_note: { de: "Vorher = nur eingefügt. Stand vor manuellen Verschiebungen.", fr: "Avant = insertion seule. État avant les déplacements manuels.", en: "Before = insertion only. State before manual moves." },
  inserted: { de: "Neue Stopps", fr: "Nouveaux arrêts", en: "New stops" },
  unplaced: { de: "Nicht einplanbare Aufträge", fr: "Commandes non planifiables", en: "Orders not placed" },
  unplaced_hint: { de: "Aufträge mit ✕ auf der Karte kannst du von Hand auf eine Tour ziehen.", fr: "Les commandes marquées ✕ peuvent être glissées sur une tournée.", en: "Orders marked ✕ on the map can be dragged onto a tour by hand." },
  skipped: { de: "Übersprungene Tour-Stopps (Adresse nicht gefunden)", fr: "Arrêts ignorés (adresse introuvable)", en: "Skipped tour stops (address not found)" },
  all_tours: { de: "Alle Touren mit Zeiten", fr: "Toutes les tournées avec horaires", en: "All tours with times" },
  geocoding: { de: "Geocoding prüfen", fr: "Vérifier le géocodage", en: "Check geocoding" },
  downloads: { de: "Herunterladen", fr: "Télécharger", en: "Download" },
  assumptions: {
    de: "Zeiten sind Schätzungen: Luftlinie × Umwegfaktor bei konstantem Tempo. Bitte vor dem Versand an Fahrer:innen prüfen.",
    fr: "Les horaires sont des estimations : vol d'oiseau × facteur de détour à vitesse constante. À vérifier avant l'envoi aux livreurs.",
    en: "Times are estimates: crow-fly distance × detour factor at constant speed. Check before sending to drivers."
  },
  col_order: { de: "Auftrag", fr: "Commande", en: "Order" },
  col_tour: { de: "Tour", fr: "Tournée", en: "Tour" },
  col_pos: { de: "Stopp Nr.", fr: "Arrêt n°", en: "Stop no." },
  col_extra: { de: "Umweg (min)", fr: "Détour (min)", en: "Detour (min)" },
  col_arrival: { de: "Ankunft", fr: "Arrivée", en: "Arrival" },
  col_address: { de: "Adresse", fr: "Adresse", en: "Address" },
  col_reason: { de: "Grund", fr: "Raison", en: "Reason" },
  col_stop: { de: "Stopp", fr: "Arrêt", en: "Stop" },
  col_window: { de: "Zeitfenster", fr: "Créneau", en: "Time window" },
  col_slack: { de: "Reserve (min)", fr: "Marge (min)", en: "Slack (min)" },
  col_before: { de: "Vorher (min)", fr: "Avant (min)", en: "Before (min)" },
  col_after: { de: "Nachher (min)", fr: "Après (min)", en: "After (min)" },
  col_saved: { de: "Gespart (min)", fr: "Gagné (min)", en: "Saved (min)" },
  col_moved: { de: "Umgestellte Stopps", fr: "Arrêts déplacés", en: "Moved stops" },
  col_status: { de: "Status", fr: "Statut", en: "Status" },
  col_found: { de: "Gefunden", fr: "Trouvé", en: "Found" },
  col_end: { de: "Ende", fr: "Fin", en: "End" },
  manual: { de: "von Hand", fr: "manuel", en: "manual" },
  new: { de: "neu", fr: "nouveau", en: "new" },
  late: { de: "zu spät", fr: "en retard", en: "late" },
  moved: { de: "umgestellt", fr: "déplacé", en: "moved" },
  stop_word: { de: "Stopp", fr: "arrêt", en: "stop" },
  no_feasible_position: { de: "passt in keine Tour (Zeitfenster)", fr: "n'entre dans aucune tournée (créneaux)", en: "fits no tour (time windows)" },
  geocode_not_found: { de: "Adresse nicht gefunden", fr: "adresse introuvable", en: "address not found" },
  geocode_uncertain: { de: "Adresse unsicher, bitte prüfen", fr: "adresse incertaine, à vérifier", en: "address uncertain, please check" },
  no_tours: { de: "keine Touren vorhanden", fr: "aucune tournée", en: "no tours" },
  moved_by_hand: { de: "von Hand verschoben", fr: "déplacé à la main", en: "moved by hand" }
};

let LANG = "de";

function t(key, vars) {
  const entry = TEXT[key];
  let text = entry ? (entry[LANG] || entry.de) : key;
  if (vars) for (const [k, v] of Object.entries(vars)) text = text.split("{" + k + "}").join(String(v));
  return text;
}

function applyTexts() {
  document.documentElement.lang = LANG;
  document.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
}
