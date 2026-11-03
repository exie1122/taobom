"""CLI: taobom parts|mock|login|scrape|plan"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from . import __version__
from .bom import BomError, apply_overrides, load_bom
from .mock import mock_listings
from .models import Listing, Shop
from .output import render
from .solver import solve

FORMAT_SUFFIX = {".html": "html", ".htm": "html", ".json": "json",
                 ".txt": "text", ".md": "md", ".markdown": "md"}


def save_listings(path: Path, shops: list[Shop], listings: list[Listing]) -> None:
    """Write listings atomically so an interrupted run cannot truncate the file."""
    data = {
        "shops": [dataclasses.asdict(s) for s in shops],
        "listings": [dataclasses.asdict(l) for l in listings],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def merge_listings(
    base_shops: dict[str, Shop],
    base_listings: list[Listing],
    new_shops: list[Shop],
    new_listings: list[Listing],
) -> tuple[list[Shop], list[Listing]]:
    """Merge a re-scrape into an existing listings file.

    Shops are matched by name as well as id, so a shop that was saved under an
    older slug is not duplicated, and listings are remapped onto the existing id.
    """
    shops = dict(base_shops)
    name_to_id = {s.name: s.id for s in shops.values()}
    remap: dict[str, str] = {}
    for shop in new_shops:
        existing = name_to_id.get(shop.name)
        if existing and existing != shop.id:
            remap[shop.id] = existing
            current = shops[existing]
            if not current.ship_from:
                current.ship_from = shop.ship_from
            if not current.url:
                current.url = shop.url
            continue
        shops[shop.id] = shop
        name_to_id[shop.name] = shop.id

    listings = list(base_listings)
    for listing in new_listings:
        listing.shop_id = remap.get(listing.shop_id, listing.shop_id)
        listings.append(listing)
    return list(shops.values()), listings


def load_listings(path: Path) -> tuple[dict[str, Shop], list[Listing]]:
    if not path.is_file():
        raise FileNotFoundError(f"listings file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("shops"), list) \
            or not isinstance(data.get("listings"), list):
        raise ValueError("listings JSON requires 'shops' and 'listings' arrays")

    shop_fields = {f.name for f in dataclasses.fields(Shop)}
    listing_fields = {f.name for f in dataclasses.fields(Listing)}
    shops: dict[str, Shop] = {}
    for index, item in enumerate(data["shops"]):
        if not isinstance(item, dict):
            raise ValueError(f"shop at index {index} must be an object")
        extra = set(item) - shop_fields
        if extra:
            raise ValueError(f"shop at index {index} has unknown fields: "
                             + ", ".join(sorted(extra)))
        try:
            shop = Shop(**item)
        except TypeError as exc:
            raise ValueError(f"invalid shop at index {index}: {exc}") from exc
        if not shop.id:
            raise ValueError(f"shop at index {index} has an empty id")
        if shop.id in shops:
            raise ValueError(f"duplicate shop id '{shop.id}'")
        shops[shop.id] = shop

    listings: list[Listing] = []
    for index, item in enumerate(data["listings"]):
        if not isinstance(item, dict):
            raise ValueError(f"listing at index {index} must be an object")
        extra = set(item) - listing_fields
        if extra:
            raise ValueError(f"listing at index {index} has unknown fields: "
                             + ", ".join(sorted(extra)))
        try:
            listing = Listing(**item)
        except TypeError as exc:
            raise ValueError(f"invalid listing at index {index}: {exc}") from exc
        if listing.shop_id not in shops:
            raise ValueError(
                f"listing at index {index} references unknown shop '{listing.shop_id}'"
            )
        if listing.price < 0:
            raise ValueError(f"listing at index {index} has a negative price")
        if listing.lot_size <= 0:
            raise ValueError(f"listing at index {index} has lot_size <= 0")
        if not 0 <= listing.confidence <= 1:
            raise ValueError(
                f"listing at index {index} confidence must be between 0 and 1"
            )
        listings.append(listing)
    return shops, listings


def _load_parts(args) -> list:
    parts = load_bom(args.bom, boards=args.boards)
    if args.overrides:
        path = Path(args.overrides)
        if not path.is_file():
            raise FileNotFoundError(f"overrides file not found: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from exc
        parts = apply_overrides(parts, data)
    return parts


def _utf8_stdout() -> None:
    """Windows consoles default to cp1252, which cannot print Chinese titles."""
    stream = sys.stdout
    if hasattr(stream, "reconfigure") and stream.encoding \
            and stream.encoding.lower() != "utf-8":
        stream.reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="taobom", description="KiCad BOM -> Taobao purchase plan")
    ap.add_argument("--version", action="version", version=f"taobom {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_parts = sub.add_parser("parts", help="show purchasable parts parsed from a BOM CSV")
    p_parts.add_argument("bom")
    p_parts.add_argument("--json", action="store_true", help="machine-readable output")

    p_mock = sub.add_parser("mock", help="generate fake shop listings for a BOM (pipeline test)")
    p_mock.add_argument("bom")
    p_mock.add_argument("-o", "--output", default="listings.json")

    sub.add_parser("login", help="open Chrome to log into Taobao once (session persists)")

    p_scrape = sub.add_parser(
        "scrape", help="search Taobao for each part -> listings.json (no LLM)")
    p_scrape.add_argument("bom")
    p_scrape.add_argument("-o", "--output", default="listings.json")
    p_scrape.add_argument("--raw-dir", default="raw", help="dump raw API payloads here")
    p_scrape.add_argument("--per-part", type=int, default=8)
    p_scrape.add_argument("--min-confidence", type=float, default=0.3,
                          help="drop candidates below this title match score (default 0.3)")
    p_scrape.add_argument("--delay", type=float, default=6.0,
                          help="seconds between searches (keep human-slow)")
    p_scrape.add_argument("--reuse-raw", action="store_true",
                          help="replay payloads already dumped in --raw-dir instead of "
                               "hitting Taobao (offline re-extract / resume)")
    p_scrape.add_argument("--only", nargs="+", metavar="PART_ID",
                          help="only (re)scrape these part ids; other parts already in "
                               "-o are kept")

    p_plan = sub.add_parser("plan", help="solve shop consolidation and write the shopping list")
    p_plan.add_argument("bom")
    p_plan.add_argument("listings")
    p_plan.add_argument("-o", "--output", default=None)
    p_plan.add_argument("--format", choices=("auto", "md", "html", "text", "json"),
                        default="auto", help="output format (auto = from -o suffix)")
    p_plan.add_argument("--max-shops", type=int, default=5,
                        help="preferred shop limit; the solver reports and relaxes it "
                             "when impossible (0 = no limit)")
    p_plan.add_argument("--strict-shops", action="store_true",
                        help="fail instead of relaxing when --max-shops is impossible")
    p_plan.add_argument("--shop-penalty", type=float, default=8.0,
                        help="CNY-equivalent hassle cost per extra shop (default 8)")
    p_plan.add_argument("--risk-weight", type=float, default=15.0,
                        help="CNY penalty at 0%% match confidence; steers the solver "
                             "toward trusted listings (default 15)")

    for p in (p_parts, p_mock, p_scrape, p_plan):
        p.add_argument("--overrides", help="purchase-spec JSON layered over the BOM "
                                           "(exclude / qty / kits / add)")
        p.add_argument("--boards", type=int, default=1,
                       help="multiply BOM quantities for N boards (default 1)")
    return ap


def _run(args) -> int:
    if args.cmd == "login":
        from .scrape import login
        return login()

    if args.boards < 1:
        raise ValueError("--boards must be a positive integer")

    parts = _load_parts(args)

    if args.cmd == "parts":
        if args.json:
            print(json.dumps(
                [dataclasses.asdict(p) for p in parts], ensure_ascii=False, indent=2))
            return 0
        for p in parts:
            print(f"{p.qty:>3} x {p.display:<40} {p.footprint}")
            if p.lcsc:
                print(f"      LCSC:   {p.lcsc}")
            if p.note:
                print(f"      note:   {p.note}")
            print(f"      search: {' | '.join(p.search_terms)}")
        units = sum(p.qty for p in parts)
        print(f"\n{len(parts)} parts · {units} units"
              + (f" · {args.boards} boards" if args.boards > 1 else ""))
        return 0

    if args.cmd == "mock":
        shops, listings = mock_listings(parts)
        save_listings(Path(args.output), shops, listings)
        print(f"{len(listings)} mock listings across {len(shops)} shops -> {args.output}")
        return 0

    if args.cmd == "scrape":
        from .scrape import scrape
        output = Path(args.output)
        only = set(args.only or [])
        base_shops: dict[str, Shop] = {}
        base_listings: list[Listing] = []
        if only and output.is_file():
            base_shops, base_listings = load_listings(output)
            base_listings = [l for l in base_listings if l.part_id not in only]
            print(f"keeping {len(base_listings)} existing listings for other parts")
        elif only:
            print("warning: --only with a new output file produces a partial plan",
                  file=sys.stderr)

        def on_snapshot(_done: int, _total: int, shops: list[Shop],
                        listings: list[Listing]) -> None:
            merged_shops, merged_listings = merge_listings(
                base_shops, base_listings, shops, listings)
            save_listings(output, merged_shops, merged_listings)

        shops, listings = scrape(
            parts, Path(args.raw_dir),
            per_part=args.per_part, min_confidence=args.min_confidence,
            delay=args.delay, reuse_raw=args.reuse_raw,
            only=args.only, on_snapshot=on_snapshot,
            shops=list(base_shops.values()))
        all_listings = base_listings + listings
        if not listings:
            print("no listings scraped — check raw dumps or login state", file=sys.stderr)
            if base_listings:
                print(f"kept the existing {len(base_listings)} listings in "
                      f"{args.output}", file=sys.stderr)
            return 1
        merged_shops, merged_listings = merge_listings(
            base_shops, base_listings, shops, listings)
        save_listings(output, merged_shops, merged_listings)
        print(f"{len(listings)} new candidate listings ({len(all_listings)} total, "
              f"{len(merged_shops)} shops) -> {args.output}")
        return 0

    if args.cmd == "plan":
        shops, listings = load_listings(Path(args.listings))
        plan = solve(parts, shops, listings,
                     max_shops=args.max_shops, shop_penalty=args.shop_penalty,
                     risk_weight=args.risk_weight, strict_shops=args.strict_shops)
        fmt = args.format
        if args.output:
            output = Path(args.output)
        else:
            suffix = "md" if fmt == "auto" else ("txt" if fmt == "text" else fmt)
            output = Path(f"plan.{suffix}")
        if fmt == "auto":
            fmt = FORMAT_SUFFIX.get(output.suffix.lower(), "md")
        text = render(plan, fmt)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")

        used = plan.by_shop()
        print(f"{len(plan.assignments)} parts from {len(used)} shops, "
              f"{len(plan.unmatched)} unmatched, total ~ CNY {plan.total:.2f}")
        if plan.cap_relaxed:
            print(f"warning: covering every part needs {plan.min_shops_required} shops "
                  f"(--max-shops {plan.requested_max_shops}); this plan uses "
                  f"{len(used)}.", file=sys.stderr)
        if plan.engine != "ilp":
            print("note: pulp/CBC unavailable, used the greedy fallback; "
                  "the result may not be cost-optimal.", file=sys.stderr)
        saving = plan.savings_vs_single_shop()
        if saving:
            print(f"single-shop alternative ~ CNY {plan.single_shop[1]:.2f}; "
                  f"this plan saves CNY {saving[1]:.2f}")
        print(f"plan -> {args.output or output}")
        return 0

    return 1


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    ap = build_parser()
    args = ap.parse_args(argv)
    try:
        return _run(args)
    except (BomError, ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
