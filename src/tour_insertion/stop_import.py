"""Stop lists in any layout (Excel or CSV) -> rows the planner understands.

Customers send their stops in their own format: one file has the address in
one column, another has street, postcode and city in three; quantities may
be split by product ("Box 50er", "Box 25er"). A *template* says which column
means what:

    address     column(s) with street and house number (joined with ", "),
                or the full address
    postcode    column with the postal code (optional)
    city        column with the city (optional)
    id          column with a unique id (optional; else 001, 002, ...)
    name_columns  column(s) shown as the stop's name (optional, joined)
    quantities  column(s) with numbers per product; their sum is the stop's
                items. Empty list: every stop has `default_items`.
    info        columns passed on unchanged to the export (contact, opening
                hours, ...), not used for planning
    labels      readable names for info columns in the export, e.g.
                {"Praxis_Klinik": "Praxis"} (optional)
    country     "CH", "FR" or "" (guess from the postal code)
    kind        "drop" or "pickup" for all stops of the file

Rows that are empty in all used columns are skipped, and so are total rows
(a used cell says "Total", "Summe", ...). A row without an address cannot be
planned: it is returned separately (reason "address_missing").
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field, fields
from datetime import date, datetime, time

from tour_insertion.load import DROP, KINDS

# parts of an address that a geocoder does not know: "1. Stock", "2. OG", "EG"
FLOOR = re.compile(r"^(\d+\.?\s*(stock|og|ug|etage|obergeschoss|untergeschoss)|eg|og|ug|"
                   r"parterre|erdgeschoss|rdc|\d+(er|e|ème)?\s*étage)$", re.IGNORECASE)
TOTAL_WORDS = {"total", "totals", "summe", "gesamt", "gesamttotal", "totaux", "somme"}
REASON_NO_ADDRESS = "address_missing"


@dataclass
class Template:
    address: list[str] = field(default_factory=list)
    postcode: str = ""
    city: str = ""
    id: str = ""
    name_columns: list[str] = field(default_factory=list)
    quantities: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    country: str = ""
    kind: str = DROP
    default_items: int = 1
    sheet: str = ""
    title: str = ""                 # label of the template, e.g. a customer

    @classmethod
    def from_json(cls, data: dict | None) -> Template:
        data = dict(data or {})
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in known}
        for key in ("address", "name_columns", "quantities", "info"):
            v = values.get(key)
            if isinstance(v, str):
                values[key] = [v] if v else []
        t = cls(**values)
        if t.kind not in KINDS:
            raise ValueError(f"kind must be 'drop' or 'pickup', got {t.kind!r}")
        if not t.address:
            raise ValueError("the template needs at least one address column")
        t.default_items = int(t.default_items)
        return t

    def columns(self) -> list[str]:
        cols = [*self.address, self.postcode, self.city, self.id, *self.name_columns,
                *self.quantities, *self.info]
        return [c for c in cols if c]


@dataclass
class Table:
    name: str
    headers: list[str]
    rows: list[dict[str, str]]          # header -> text, "" for empty

    def preview(self, n: int = 5) -> list[list[str]]:
        return [[r.get(h, "") for h in self.headers] for r in self.rows[:n]]


# --------------------------------------------------------------------------
# Reading files
# --------------------------------------------------------------------------

def cell_text(value) -> str:
    """Excel cell -> text. 2.0 -> '2', dates -> ISO, None -> ''."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M") if (value.hour or value.minute) else value.date().isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    return str(value).strip()


def _unique_headers(raw: list) -> list[str]:
    headers, seen = [], {}
    for i, h in enumerate(raw, start=1):
        name = cell_text(h) or f"column {i}"
        if name in seen:
            seen[name] += 1
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
        headers.append(name)
    return headers


def _table(name: str, raw_rows: list[list]) -> Table:
    # header = first row with any content
    while raw_rows and not any(cell_text(v) for v in raw_rows[0]):
        raw_rows = raw_rows[1:]
    if not raw_rows:
        return Table(name, [], [])
    width = max(len(r) for r in raw_rows)
    headers = _unique_headers(list(raw_rows[0]) + [None] * (width - len(raw_rows[0])))
    rows = []
    for raw in raw_rows[1:]:
        cells = [cell_text(v) for v in raw] + [""] * (width - len(raw))
        rows.append(dict(zip(headers, cells)))
    # drop trailing columns that are empty everywhere and have no header
    keep = [h for i, h in enumerate(headers)
            if not h.startswith("column ") or any(r[h] for r in rows)]
    return Table(name, keep, [{h: r[h] for h in keep} for r in rows])


def read_tables(data: bytes, filename: str) -> list[Table]:
    """All sheets of an Excel file, or the one table of a CSV file."""
    lower = filename.lower()
    if lower.endswith((".xlsx", ".xlsm")):
        try:
            import openpyxl
        except ImportError as error:              # pragma: no cover
            raise ValueError("reading Excel files needs the package openpyxl") from error
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as error:                # zip/xml errors of a broken file
            raise ValueError(f"{filename}: not a readable Excel file ({error})") from error
        tables = [_table(ws.title, [list(r) for r in ws.iter_rows(values_only=True)])
                  for ws in wb.worksheets]
        wb.close()
        return [t for t in tables if t.headers]
    if lower.endswith(".xls"):
        raise ValueError(f"{filename}: old Excel format (.xls). Save it as .xlsx and try again.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")              # Excel's "CSV" on Windows
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    raw = list(csv.reader(io.StringIO(text), dialect))
    return [_table(filename, raw)]


# --------------------------------------------------------------------------
# Guessing a template from the headers
# --------------------------------------------------------------------------

_PATTERNS = {
    "postcode": r"\b(plz|postleitzahl|zip|postal|code ?postal|cp|npa)\b|plz$",
    "city": r"\b(ort|stadt|city|ville|localit|commune|gemeinde)\b|(?<!stand)ort$",
    "address": r"adress|address|strasse|straße|street|rue\b|adresse",
    "id": r"^(nr\.?|no\.?|id|nummer|number|n°|standort|code)$",
    "name": r"standort|name|praxis|klinik|firma|kunde|client|site|empf",
}


def _match(kind: str, header: str) -> bool:
    return re.search(_PATTERNS[kind], header.strip().lower()) is not None


def _numeric(table: Table, header: str) -> bool:
    values = [r[header] for r in table.rows if r.get(header)]
    return bool(values) and all(re.fullmatch(r"-?\d+(\.0+)?", v) for v in values)


def guess_template(table: Table) -> dict:
    """A first guess, to be checked by the user."""
    h = table.headers
    used: set[str] = set()

    def take(kind, candidates=None):
        for col in candidates or h:
            if col not in used and _match(kind, col):
                used.add(col)
                return col
        return ""

    postcode = take("postcode")
    city = take("city")
    address = [c for c in h if c not in used and _match("address", c)]
    used.update(address)
    id_ = take("id")
    if id_ and len({r[id_] for r in table.rows if r[id_]}) < len([r for r in table.rows if r[id_]]):
        used.discard(id_)
        id_ = ""                                  # not unique, e.g. always "1"
    name = [c for c in h if c not in used and _match("name", c)][:2]
    used.update(name)
    numbers_only = [c for c in h if c not in used and _match("id", c)]   # "Nr." etc.
    used.update(numbers_only)
    quantities = [c for c in h if c not in used and _numeric(table, c)]
    used.update(quantities)
    info = [c for c in h if c not in used and any(r[c] for r in table.rows)]
    return {"sheet": table.name, "address": address[:1] if postcode or city else address,
            "postcode": postcode, "city": city, "id": id_, "name_columns": name,
            "quantities": quantities, "info": info, "country": "", "kind": DROP,
            "default_items": 1}


def inspect(data: bytes, filename: str) -> dict:
    """What the web page shows to set up a template."""
    tables = read_tables(data, filename)
    if not tables:
        raise ValueError(f"{filename}: no data found")
    return {"sheets": [{"name": t.name, "headers": t.headers, "rows": len(t.rows),
                        "preview": t.preview(), "guess": guess_template(t)} for t in tables],
            "guess": guess_template(tables[0])}


# --------------------------------------------------------------------------
# Rows -> order rows
# --------------------------------------------------------------------------

def _quantity(text: str, where: str, column: str) -> int:
    if not text:
        return 0
    m = re.match(r"\s*(-?\d+)", text)            # "4", "4.0", "4 Stück"
    if not m:
        raise ValueError(f"{where}: {column} is not a number: {text!r}")
    value = int(m.group(1))
    if value < 0:
        raise ValueError(f"{where}: {column} must not be negative, got {value}")
    return value


def _is_total(row: dict[str, str], template: Template) -> bool:
    for col in [template.id, *template.name_columns, *template.address]:
        if col and row.get(col, "").strip().lower().rstrip(":") in TOTAL_WORDS:
            return True
    return False


def order_rows(data: bytes, filename: str, template: Template
               ) -> tuple[list[dict], list[dict]]:
    """(rows for orders_from_rows, rows without address).

    Every row gets order_id, name, address, country, items, type and the
    hidden keys _name, _quantities, _info (see csv_io.extras_from_row).
    """
    tables = read_tables(data, filename)
    if not tables:
        raise ValueError(f"{filename}: no data found")
    if template.sheet:
        table = next((t for t in tables if t.name == template.sheet), None)
        if table is None:
            raise ValueError(f"{filename}: no sheet named {template.sheet!r} "
                             f"(sheets: {', '.join(t.name for t in tables)})")
    else:
        table = tables[0]
    missing = [c for c in template.columns() if c not in table.headers]
    if missing:
        raise ValueError(f"{filename}: column(s) not found: {', '.join(missing)}")

    out, no_address, ids = [], [], set()
    number = 0
    for line, row in enumerate(table.rows, start=2):
        where = f"{filename}:{line}"
        if not any(row.get(c, "") for c in template.columns()) or _is_total(row, template):
            continue
        number += 1
        parts = [p.strip() for c in template.address for p in row[c].split(",") if p.strip()]
        floors = [p for p in parts if FLOOR.match(p)]
        street = ", ".join(p for p in parts if not FLOOR.match(p))
        place = " ".join(v for v in (row.get(template.postcode, ""),
                                     row.get(template.city, "")) if v)
        address = ", ".join(v for v in (street, place) if v)
        order_id = row[template.id] if template.id and row[template.id] else f"{number:03d}"
        if order_id in ids:
            raise ValueError(f"{where}: id {order_id!r} appears twice")
        ids.add(order_id)
        quantities = {c: _quantity(row[c], where, c) for c in template.quantities}
        items = sum(quantities.values()) if template.quantities else template.default_items
        name = " ".join(dict.fromkeys(row[c] for c in template.name_columns if row[c]))
        result = {
            "order_id": order_id, "address": address, "country": template.country,
            "lat": "", "lon": "", "tw_start": "", "tw_end": "", "service_min": "",
            "items": str(items), "type": template.kind, "_where": where,
            "_name": name,
            "_quantities": {c: v for c, v in quantities.items()},
            "_info": {template.labels.get(c) or c: row[c] for c in template.info if row[c]},
        }
        if floors:                       # kept for the driver, not geocoded
            result["_info"]["floor"] = ", ".join(floors)
        (out if street else no_address).append(result)
    return out, no_address
