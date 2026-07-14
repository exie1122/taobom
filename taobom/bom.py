"""Parse a KiCad BOM CSV (kicad-cli sch export bom) into purchasable Parts."""
from __future__ import annotations

import csv
import re
from pathlib import Path

from .models import Part

# Footprint substrings that mean "not a purchasable component"
NON_PURCHASABLE_FOOTPRINT = ("TestPoint", "MountingHole", "Fiducial", "Logo", "NetTie")
# Reference prefixes that are never purchased
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
    if re.fullmatch(r"[\d.]+[pnum]?F", v, re.IGNORECASE) and p:
        terms.append(f"贴片电容 {p} {v}")
    elif re.fullmatch(r"[\d.]+[kKmM]?(\d+)?", v.replace("Ω", "").replace("R", "")) and p:
        terms.append(f"贴片电阻 {p} {v}")
    if v and v not in terms:
        terms.append(f"{v} {p}".strip())
    return terms


def load_bom(path: str | Path) -> list[Part]:
    parts: list[Part] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            refs = _expand_refs(row.get("Reference", ""))
            if not refs:
                continue
            footprint = row.get("Footprint", "").strip()
            if not _is_purchasable(refs, footprint):
                continue
            value = row.get("Value", "").strip()
            mpn = row.get("MPN", "").strip()
            pkg_match = PACKAGE_RE.search(footprint)
            package = pkg_match.group(1) if pkg_match else ""
            parts.append(
                Part(
                    id=_slug(value, package),
                    refs=refs,
                    value=value,
                    footprint=footprint,
                    qty=len(refs),
                    mpn=mpn,
                    package=package,
                    search_terms=_search_terms(value, package, mpn),
                )
            )
    return parts
