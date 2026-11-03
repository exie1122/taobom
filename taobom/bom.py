"""Parse a KiCad / JLCPCB BOM CSV into purchasable Parts."""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

from .models import Part


class BomError(ValueError):
    """The CSV is readable but does not look like a usable BOM."""


# Footprint substrings that mean "not a purchasable component"
NON_PURCHASABLE_FOOTPRINT = (
    "TestPoint", "MountingHole", "Fiducial", "Logo", "NetTie", "SolderJumperOpen",
)
# Reference prefixes that are never purchased
NON_PURCHASABLE_REF = ("TP", "H", "FID", "MK", "LOGO")

# Column spellings seen from kicad-cli, KiCad's Python BOM scripts, and JLCPCB.
COLUMN_ALIASES = {
    "refs": ("Reference", "References", "Refs", "Ref", "Designator", "Designators"),
    "value": ("Value", "Comment", "Val", "Comment Value"),
    "footprint": ("Footprint", "Package", "Footprint (Package)", "Pattern"),
    "mpn": ("MPN", "Mfr Part #", "Manufacturer Part Number", "Manufacturer Part",
            "Part Number", "Manufacturer Part Number (MPN)"),
    "lcsc": ("LCSC", "LCSC Part #", "LCSC Part", "LCSC Part Number", "JLCPCB Part #"),
    "dnp": ("DNP", "Exclude from BOM", "Do not populate"),
}

PACKAGE_RE = re.compile(
    r"(0201|0402|0603|0805|1206|1210|2010|2512"
    r"|SOT-23-[56]|SOT-23|SOT-89|SOT-223|SOD-123|SOD-323"
    r"|TO-220|TO-252|TO-92"
    r"|QFN-\d+|LQFP-\d+|TQFP-\d+|TSSOP-\d+|SSOP-\d+|SOIC-\d+|SOP-\d+|MSOP-\d+"
    r"|DIP-\d+|BGA-\d+)",
    re.IGNORECASE,
)

_HEADER_NORMALISE = re.compile(r"[^a-z0-9]+")
TRUTHY = {"1", "true", "yes", "y", "dnp", "dni", "exclude", "excluded"}


def _normalise_header(name: str) -> str:
    return _HEADER_NORMALISE.sub("", (name or "").lower())


def _resolve_columns(fieldnames: list[str] | None) -> dict[str, str]:
    """Map logical names to the actual header spellings in this CSV."""
    available = [f for f in (fieldnames or []) if f]
    by_normalised = {_normalise_header(f): f for f in available}
    resolved: dict[str, str] = {}
    for logical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            actual = by_normalised.get(_normalise_header(alias))
            if actual:
                resolved[logical] = actual
                break
    if "refs" not in resolved:
        raise BomError(
            "BOM is missing a reference/designator column. Expected one of: "
            + ", ".join(ALIASES_TEXT["refs"])
            + f". Columns found: {', '.join(available) or '(none)'}.\n"
            "For KiCad run: kicad-cli sch export bom --fields "
            "\"Reference,Value,Footprint,MPN\" --group-by Value,Footprint <schematic>"
        )
    return resolved


ALIASES_TEXT = {k: ", ".join(v[:4]) for k, v in COLUMN_ALIASES.items()}


def _expand_refs(field: str) -> list[str]:
    """'C2-C7' -> C2..C7; 'R3,R4' -> [R3, R4]; unannotated 'J_CAM_L?' kept as-is."""
    refs: list[str] = []
    for token in (t.strip() for t in field.split(",") if t.strip()):
        m = re.fullmatch(r"([A-Za-z_]+)(\d+)-([A-Za-z_]+)?(\d+)", token)
        if m and (m.group(3) is None or m.group(3) == m.group(1)):
            prefix, lo, hi = m.group(1), int(m.group(2)), int(m.group(4))
            if hi >= lo and hi - lo <= 10_000:  # guard against typos like R1-R99999
                refs.extend(f"{prefix}{i}" for i in range(lo, hi + 1))
                continue
        refs.append(token)
    return refs


def _ref_prefix(ref: str) -> str:
    m = re.match(r"([A-Za-z]+)", ref)
    return m.group(1).upper() if m else ""


def _is_purchasable(refs: list[str], footprint: str) -> bool:
    if any(tag.lower() in footprint.lower() for tag in NON_PURCHASABLE_FOOTPRINT):
        return False
    prefixes = {_ref_prefix(r) for r in refs}
    return not prefixes.issubset(set(NON_PURCHASABLE_REF))


def _is_dnp(value: str) -> bool:
    return value.strip().lower() in TRUTHY


def _slug(value: str, package: str) -> str:
    raw = f"{value}_{package}" if package else value
    return re.sub(r"[^A-Za-z0-9.]+", "_", raw).strip("_")


def _search_terms(value: str, package: str, mpn: str, lcsc: str = "") -> list[str]:
    """Queries to try on Taobao, best first. Chinese terms match more listings."""
    terms: list[str] = []
    if mpn:
        terms.append(mpn)
    elif lcsc:
        terms.append(lcsc)
    v, p = value.strip(), package
    # Match a leading component value so JLCPCB-style comments
    # ("100nF 50V X7R") still produce a usable Chinese query.
    cap = re.match(r"^([\d.]+[pnum]?F)\b", v, re.IGNORECASE)
    resistor = re.match(r"^([\d.]+[kKmMrR]?[\d]*)\b", v.replace("Ω", ""))
    if cap and p:
        terms.append(f"贴片电容 {p} {cap.group(1)}")
    elif resistor and p:
        terms.append(f"贴片电阻 {p} {resistor.group(1)}")
    if v:
        terms.append(f"{v} {p}".strip())
    if lcsc and lcsc not in terms:
        terms.append(lcsc)
    # de-duplicate, preserve order
    seen, out = set(), []
    for t in terms:
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def apply_overrides(parts: list[Part], data: dict) -> list[Part]:
    """Layer a purchase spec over the raw BOM.

    Overrides JSON sections:
      exclude: [part_id]            drop parts (already owned / not purchasable)
      qty:     {part_id: n}         explicit purchase qty (spares, multiple builds)
      search:  {part_id: [terms]}   replace auto-generated Taobao search terms
      kits:    [{id, value, covers: [part_id], qty, note, search_terms}]
               replace covered parts with one assortment-kit line
      add:     [{id, value, qty, mpn?, package?, note?, search_terms?}]
               parts not in the BOM at all
    """
    if not isinstance(data, dict):
        raise ValueError("overrides must be a JSON object")

    known = {p.id for p in parts}
    for section in ("kits", "add"):
        value = data.get(section, [])
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ValueError(f"overrides '{section}' must be a list of objects")
    for section in ("exclude", "qty", "search"):
        value = data.get(section, [] if section == "exclude" else {})
        expected = list if section == "exclude" else dict
        if not isinstance(value, expected):
            raise ValueError(
                f"overrides '{section}' must be a {expected.__name__}, "
                f"got {type(value).__name__}"
            )
        for pid in value:
            if pid not in known:
                print(f"warning: overrides {section}: unknown part id '{pid}'",
                      file=sys.stderr)

    excl = set(data.get("exclude", []))
    parts = [p for p in parts if p.id not in excl]

    for p in parts:
        if p.id in data.get("qty", {}):
            qty = data["qty"][p.id]
            if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
                raise ValueError(f"override qty for '{p.id}' must be a positive integer")
            p.qty = qty
        if p.id in data.get("search", {}):
            terms = data["search"][p.id]
            if not isinstance(terms, list) or not all(isinstance(t, str) and t.strip() for t in terms):
                raise ValueError(
                    f"override search terms for '{p.id}' must be a non-empty list of strings"
                )
            p.search_terms = list(terms)

    out: list[Part] = []
    covered: set[str] = set()
    kit_ids: set[str] = set()
    for k in data.get("kits", []):
        for field in ("id", "value"):
            if not k.get(field):
                raise ValueError(f"every kit needs a non-empty '{field}'")
        if k["id"] in kit_ids:
            raise ValueError(f"duplicate kit id '{k['id']}'")
        kit_ids.add(k["id"])
        covers = list(k.get("covers", []))
        for pid in covers:
            if pid not in known:
                print(f"warning: kit '{k['id']}' covers unknown part id '{pid}'",
                      file=sys.stderr)
            if pid in covered:
                print(f"warning: part '{pid}' is covered by more than one kit",
                      file=sys.stderr)
        covered |= set(covers)
        out.append(Part(
            id=k["id"], refs=covers, value=k["value"],
            footprint="", qty=int(k.get("qty", 1)), package=k.get("package", ""),
            search_terms=list(k.get("search_terms", [])), note=k.get("note", ""),
        ))
    parts = [p for p in parts if p.id not in covered]

    existing_ids = known | kit_ids
    for a in data.get("add", []):
        for field in ("id", "value"):
            if not a.get(field):
                raise ValueError(f"every added part needs a non-empty '{field}'")
        if a["id"] in existing_ids:
            raise ValueError(
                f"added part id '{a['id']}' collides with an existing part id"
            )
        qty = a.get("qty")
        if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
            raise ValueError(f"added part '{a['id']}' needs a positive integer qty")
        out.append(Part(
            id=a["id"], refs=[], value=a["value"], footprint="",
            qty=qty, mpn=a.get("mpn", ""), package=a.get("package", ""),
            search_terms=list(a.get("search_terms", [])), note=a.get("note", ""),
            lcsc=a.get("lcsc", ""),
        ))
    return parts + out


def _merge_rows(rows: list[Part]) -> list[Part]:
    """Consolidate rows describing the same part (ungrouped BOM exports)."""
    merged: dict[tuple[str, str, str, str], Part] = {}
    for p in rows:
        key = (p.value, p.package, p.mpn, p.lcsc)
        existing = merged.get(key)
        if existing is None:
            merged[key] = p
            continue
        existing.qty += p.qty
        for ref in p.refs:
            if ref not in existing.refs:
                existing.refs.append(ref)
        if p.note and p.note not in existing.note:
            existing.note = "; ".join(x for x in (existing.note, p.note) if x)
    return list(merged.values())


def load_bom(path: str | Path, boards: int = 1) -> list[Part]:
    """Read a BOM CSV and return one Part per distinct purchasable line item.

    ``boards`` multiplies every quantity, so ``--boards 5`` plans five builds
    without editing the overrides file.
    """
    if not isinstance(boards, int) or isinstance(boards, bool) or boards < 1:
        raise ValueError("boards must be a positive integer")

    bom_path = Path(path)
    if not bom_path.is_file():
        raise FileNotFoundError(f"BOM file not found: {bom_path}")

    with open(bom_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        columns = _resolve_columns(reader.fieldnames)

        rows: list[Part] = []
        skipped = 0
        for row in reader:
            refs_field = row.get(columns["refs"], "") or ""
            refs = _expand_refs(refs_field)
            if not refs:
                skipped += 1
                continue
            footprint = (row.get(columns.get("footprint", ""), "") or "").strip()
            dnp = (row.get(columns["dnp"], "") if "dnp" in columns else "") or ""
            if _is_dnp(dnp) or not _is_purchasable(refs, footprint):
                skipped += 1
                continue
            value = (row.get(columns.get("value", ""), "") or "").strip()
            mpn = (row.get(columns.get("mpn", ""), "") or "").strip()
            lcsc = (row.get(columns.get("lcsc", ""), "") or "").strip()
            if not value and not mpn:
                skipped += 1
                continue
            pkg_match = PACKAGE_RE.search(footprint)
            package = pkg_match.group(1) if pkg_match else ""
            rows.append(Part(
                id=_slug(value or mpn or lcsc, package),
                refs=refs,
                value=value,
                footprint=footprint,
                qty=len(refs) * boards,
                mpn=mpn,
                package=package,
                search_terms=_search_terms(value, package, mpn, lcsc),
                lcsc=lcsc,
            ))

    parts = _merge_rows(rows)
    if not parts:
        hint = (
            "No purchasable parts found. Non-component rows (test points, "
            "mounting holes, fiducials, DNP) are skipped by design."
            if skipped
            else "No parts found. The BOM appears to be empty."
        )
        raise BomError(
            f"{hint}\nColumns detected: {', '.join(columns.values()) or '(none)'}.\n"
            "For KiCad run: kicad-cli sch export bom --fields "
            "\"Reference,Value,Footprint,MPN\" --group-by Value,Footprint <schematic>"
        )

    # Keep ids unique even if two distinct rows normalise to the same slug.
    taken: set[str] = set()
    for p in parts:
        base = p.id
        candidate, n = base, 1
        while candidate in taken:
            n += 1
            candidate = f"{base}-{n}"
        p.id = candidate
        taken.add(candidate)
    return parts
