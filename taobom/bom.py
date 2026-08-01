"""Parse a KiCad BOM CSV (kicad-cli sch export bom) into purchasable Parts."""
from __future__ import annotations

import csv
import hashlib
import re
import sys
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path

from .models import Part

NON_PURCHASABLE_FOOTPRINT = ("TestPoint", "MountingHole", "Fiducial", "Logo", "NetTie")
NON_PURCHASABLE_REF = ("TP", "H", "FID", "MK", "LOGO")

PACKAGE_RE = re.compile(
    r"(0201|0402|0603|0805|1206|1210|2010|2512"
    r"|SOT-23-[56]|SOT-23|SOT-89|SOT-223|SOD-123|SOD-323"
    r"|TO-220|TO-252|TO-92"
    r"|QFN-\d+|LQFP-\d+|TQFP-\d+|TSSOP-\d+|SSOP-\d+|SOIC-\d+|SOP-\d+|MSOP-\d+"
    r"|DIP-\d+|BGA-\d+)",
    re.IGNORECASE,
)


def _expand_refs(field: str) -> list[str]:
    """'C2-C7' -> C2..C7; 'R3,R4' -> [R3, R4]; unannotated 'J_CAM_L?' kept as-is."""
    refs: list[str] = []
    for token in (t.strip() for t in field.split(",") if t.strip()):
        m = re.fullmatch(r"([A-Za-z_]+)(\d+)-([A-Za-z_]+)?(\d+)", token)
        if m and (m.group(3) is None or m.group(3) == m.group(1)):
            prefix, lo, hi = m.group(1), int(m.group(2)), int(m.group(4))
            if hi >= lo:
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


def _slug(value: str, package: str) -> str:
    raw = f"{value}_{package}" if package else value
    return re.sub(r"[^A-Za-z0-9.]+", "_", raw).strip("_")


def _search_terms(value: str, package: str, mpn: str) -> list[str]:
    """Queries to try on Taobao, best first. Chinese terms match more listings."""
    terms: list[str] = []
    if mpn:
        terms.append(mpn)
    v, p = value.strip(), package
    cap = re.match(r"^([\d.]+[pnum]?F)\b", v, re.IGNORECASE)
    resistor = re.match(r"^([\d.]+[kKmMrR]?[\d]*)\b", v.replace("Ω", ""))
    if cap and p:
        terms.append(f"贴片电容 {p} {cap.group(1)}")
    elif resistor and p:
        terms.append(f"贴片电阻 {p} {resistor.group(1)}")
    if v and v not in terms:
        terms.append(f"{v} {p}".strip())
    return terms


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
    for section in ("exclude", "qty", "search"):
        value = data.get(section, [] if section == "exclude" else {})
        expected = list if section == "exclude" else dict
        if not isinstance(value, expected):
            raise ValueError(f"overrides '{section}' must be a {expected.__name__}")
        for pid in value:
            if pid not in known:
                print(f"warning: overrides {section}: unknown part id '{pid}'", file=sys.stderr)

    excl = set(data.get("exclude", []))
    parts = [replace(p, refs=list(p.refs), search_terms=list(p.search_terms))
             for p in parts if p.id not in excl]

    for p in parts:
        if p.id in data.get("qty", {}):
            p.qty = int(data["qty"][p.id])
            if p.qty <= 0:
                raise ValueError(f"override qty for '{p.id}' must be positive")
        if p.id in data.get("search", {}):
            terms = data["search"][p.id]
            if not isinstance(terms, list) or not all(isinstance(t, str) and t.strip() for t in terms):
                raise ValueError(f"override search terms for '{p.id}' must be non-empty strings")
            p.search_terms = list(terms)

    out: list[Part] = []
    covered: set[str] = set()
    kits = data.get("kits", [])
    additions = data.get("add", [])
    if not isinstance(kits, list) or not isinstance(additions, list):
        raise ValueError("overrides 'kits' and 'add' must be lists")
    for k in kits:
        if not isinstance(k, dict) or not k.get("id") or not k.get("value"):
            raise ValueError("each kit requires non-empty 'id' and 'value'")
        unknown = set(k.get("covers", [])) - known
        if unknown:
            print(f"warning: kit '{k['id']}' covers unknown ids: {', '.join(sorted(unknown))}",
                  file=sys.stderr)
        covered |= set(k.get("covers", []))
        qty = int(k.get("qty", 1))
        if qty <= 0:
            raise ValueError(f"kit qty for '{k['id']}' must be positive")
        out.append(Part(
            id=k["id"], refs=list(k.get("covers", [])), value=k["value"],
            footprint="", qty=qty, package=k.get("package", ""),
            search_terms=list(k.get("search_terms", [])), note=k.get("note", ""),
        ))
    parts = [p for p in parts if p.id not in covered]

    for a in additions:
        if not isinstance(a, dict) or not a.get("id") or not a.get("value") or "qty" not in a:
            raise ValueError("each added part requires non-empty 'id', 'value', and 'qty'")
        qty = int(a["qty"])
        if qty <= 0:
            raise ValueError(f"added-part qty for '{a['id']}' must be positive")
        out.append(Part(
            id=a["id"], refs=[], value=a["value"], footprint="",
            qty=qty, mpn=a.get("mpn", ""), package=a.get("package", ""),
            search_terms=list(a.get("search_terms", [])), note=a.get("note", ""),
        ))
    result = parts + out
    ids = [p.id for p in result]
    duplicates = sorted({pid for pid in ids if ids.count(pid) > 1})
    if duplicates:
        raise ValueError(f"duplicate part ids after overrides: {', '.join(duplicates)}")
    return result


def load_bom(path: str | Path) -> list[Part]:
    grouped: OrderedDict[tuple[str, str, str], list[str]] = OrderedDict()
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = set(reader.fieldnames or [])
        reference_field = next(
            (name for name in ("Reference", "Refs", "Designator") if name in fields),
            None,
        )
        value_field = next(
            (name for name in ("Value", "Comment") if name in fields), None
        )
        footprint_field = "Footprint" if "Footprint" in fields else None
        if not reference_field or not value_field or not footprint_field:
            expected = (
                "Reference/Refs/Designator, Value/Comment, and Footprint"
            )
            raise ValueError(f"BOM is missing required columns ({expected})")
        for row_num, row in enumerate(reader, start=2):
            refs = _expand_refs(row.get(reference_field, ""))
            if not refs:
                continue
            footprint = row.get(footprint_field, "").strip()
            if not _is_purchasable(refs, footprint):
                continue
            value = row.get(value_field, "").strip()
            if not value:
                raise ValueError(f"BOM row {row_num} has no Value")
            mpn = row.get("MPN", "").strip()
            key = (value, footprint, mpn)
            current = grouped.setdefault(key, [])
            current.extend(ref for ref in refs if ref not in current)

    records: list[tuple[tuple[str, str, str], list[str], str]] = []
    base_counts: dict[str, int] = {}
    for key, refs in grouped.items():
        value, footprint, mpn = key
        pkg_match = PACKAGE_RE.search(footprint)
        package = pkg_match.group(1) if pkg_match else ""
        base = _slug(value, package)
        records.append((key, refs, package))
        base_counts[base] = base_counts.get(base, 0) + 1

    parts: list[Part] = []
    for (value, footprint, mpn), refs, package in records:
        base = _slug(value, package)
        if base_counts[base] > 1:
            digest = hashlib.sha1(
                f"{value}\0{footprint}\0{mpn}".encode("utf-8")
            ).hexdigest()[:8]
            part_id = f"{base}__{digest}"
        else:
            part_id = base
        parts.append(Part(
            id=part_id, refs=refs, value=value, footprint=footprint,
            qty=len(refs), mpn=mpn, package=package,
            search_terms=_search_terms(value, package, mpn),
        ))
    return parts
