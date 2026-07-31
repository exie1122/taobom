"""CLI: taobom parts|mock|plan"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from .bom import apply_overrides, load_bom
from .mock import mock_listings
from .models import Listing, Shop
from .output import render_plan
from .solver import solve


def save_listings(path: Path, shops: list[Shop], listings: list[Listing]) -> None:
    data = {
        "shops": [dataclasses.asdict(s) for s in shops],
        "listings": [dataclasses.asdict(l) for l in listings],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_listings(path: Path) -> tuple[dict[str, Shop], list[Listing]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("shops"), list) \
            or not isinstance(data.get("listings"), list):
        raise ValueError("listings JSON requires 'shops' and 'listings' arrays")
    shops: dict[str, Shop] = {}
    for index, item in enumerate(data["shops"]):
        try:
            shop = Shop(**item)
        except (TypeError, KeyError) as exc:
            raise ValueError(f"invalid shop at index {index}: {exc}") from exc
        if not shop.id or shop.id in shops:
            raise ValueError(f"shop ids must be non-empty and unique: '{shop.id}'")
        shops[shop.id] = shop
    listings: list[Listing] = []
    for index, item in enumerate(data["listings"]):
        try:
            listing = Listing(**item)
        except (TypeError, KeyError) as exc:
            raise ValueError(f"invalid listing at index {index}: {exc}") from exc
        if listing.shop_id not in shops:
            raise ValueError(
                f"listing at index {index} references unknown shop '{listing.shop_id}'"
            )
        if listing.price < 0 or listing.lot_size <= 0:
            raise ValueError(f"listing at index {index} has invalid price or lot_size")
        if not 0 <= listing.confidence <= 1:
            raise ValueError(f"listing at index {index} confidence must be between 0 and 1")
        listings.append(listing)
    return shops, listings


def main(argv: list[str] | None = None) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="taobom", description="KiCad BOM -> Taobao purchase plan")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_parts = sub.add_parser("parts", help="show purchasable parts parsed from a BOM CSV")
    p_parts.add_argument("bom")

    p_mock = sub.add_parser("mock", help="generate fake shop listings for a BOM (pipeline test)")
    p_mock.add_argument("bom")
    p_mock.add_argument("-o", "--output", default="listings.json")

    sub.add_parser("login", help="open Chrome to log into Taobao once (session persists)")

    p_scrape = sub.add_parser("scrape", help="search Taobao for each part -> listings.json")
    p_scrape.add_argument("bom")
    p_scrape.add_argument("-o", "--output", default="listings.json")
    p_scrape.add_argument("--raw-dir", default="raw", help="dump raw API payloads here")
    p_scrape.add_argument("--per-part", type=int, default=20)
    p_scrape.add_argument("--delay", type=float, default=6.0,
                          help="seconds between searches (keep human-slow)")
    p_scrape.add_argument("--retries", type=int, default=2,
                          help="retries when a search returns no parseable API results")
    p_scrape.add_argument("--min-confidence", type=float, default=0.3)

    p_verify = sub.add_parser(
        "verify", help="open item pages and confirm exact SKU, price, and lot size"
    )
    p_verify.add_argument("listings")
    p_verify.add_argument("-o", "--output", default="listings.verified.json")
    p_verify.add_argument("--part", default="", help="verify only this part id")
    p_verify.add_argument(
        "--uncertain-only", action="store_true",
        help="limit the pass to uncertain or not-yet-verified listings",
    )

    p_gui = sub.add_parser("gui", help="open the simple results and cart-control GUI")
    p_gui.add_argument(
        "session", nargs="?",
        help="GUI session JSON (omit to open the TaoBOM project launcher)",
    )

    p_search_session = sub.add_parser(
        "search-session", help="scrape and optimize listings for a GUI session"
    )
    p_search_session.add_argument("session")

    p_plan = sub.add_parser("plan", help="solve shop consolidation and write the shopping list")
    p_plan.add_argument("bom")
    p_plan.add_argument("listings")
    p_plan.add_argument("-o", "--output", default="plan.md")
    p_plan.add_argument("--max-shops", type=int, default=5)
    p_plan.add_argument("--shop-penalty", type=float, default=8.0,
                        help="CNY-equivalent hassle cost per extra shop (default 8)")
    p_plan.add_argument("--risk-weight", type=float, default=15.0,
                        help="CNY penalty at 0%% match confidence; steers the solver "
                             "toward trusted listings (default 15)")

    for p in (p_parts, p_mock, p_scrape, p_plan):
        p.add_argument("--overrides", help="purchase-spec JSON layered over the BOM "
                                           "(exclude / qty / kits / add)")

    args = ap.parse_args(argv)

    if args.cmd == "login":
        from .scrape import login
        return login()

    if args.cmd == "verify":
        try:
            shops, listings = load_listings(Path(args.listings))
            from .verify import verify_listings
            verified = verify_listings(
                listings, part_id=args.part, uncertain_only=args.uncertain_only
            )
            save_listings(Path(args.output), list(shops.values()), verified)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(
            f"{sum(item.verified for item in verified)}/{len(verified)} listings verified "
            f"-> {args.output}"
        )
        return 0

    if args.cmd == "gui":
        try:
            from .gui import run_gui
            return run_gui(Path(args.session) if args.session else None)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    if args.cmd == "search-session":
        try:
            from .gui import search_session
            return search_session(args.session)
        except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    try:
        parts = load_bom(args.bom)
        if args.overrides:
            parts = apply_overrides(
                parts, json.loads(Path(args.overrides).read_text(encoding="utf-8"))
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.cmd == "parts":
        for p in parts:
            print(f"{p.qty:>3} x {p.display:<40} {p.footprint}")
            if p.note:
                print(f"      note:   {p.note}")
            print(f"      search: {' | '.join(p.search_terms)}")
        return 0

    if args.cmd == "mock":
        shops, listings = mock_listings(parts)
        save_listings(Path(args.output), shops, listings)
        print(f"{len(listings)} mock listings across {len(shops)} shops -> {args.output}")
        return 0

    if args.cmd == "scrape":
        if args.retries < 0 or args.per_part < 1 or args.delay < 0 \
                or not 0 <= args.min_confidence <= 1:
            print(
                "error: retries/delay must be non-negative, per-part must be positive, "
                "and min-confidence must be 0..1",
                file=sys.stderr,
            )
            return 2
        from .scrape import scrape
        shops, listings = scrape(parts, Path(args.raw_dir),
                                 per_part=args.per_part,
                                 min_confidence=args.min_confidence,
                                 delay=args.delay, retries=args.retries)
        if not listings:
            print("no listings scraped — check raw dumps or login state", file=sys.stderr)
            return 1
        save_listings(Path(args.output), shops, listings)
        print(f"{len(listings)} candidate listings across {len(shops)} shops -> {args.output}")
        return 0

    if args.cmd == "plan":
        try:
            shops, listings = load_listings(Path(args.listings))
            plan = solve(parts, shops, listings,
                         max_shops=args.max_shops, shop_penalty=args.shop_penalty,
                         risk_weight=args.risk_weight)
        except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        text = render_plan(plan)
        Path(args.output).write_text(text, encoding="utf-8")
        used = plan.by_shop()
        print(f"{len(plan.assignments)} parts from {len(used)} shops, "
              f"{len(plan.unmatched)} unmatched, total ~ CNY {plan.total:.2f}")
        print(f"plan -> {args.output}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
