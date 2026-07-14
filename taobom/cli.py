"""CLI: taobom parts|mock|plan"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from .bom import load_bom
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
    shops = {d["id"]: Shop(**d) for d in data["shops"]}
    return shops, [Listing(**d) for d in data["listings"]]


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252, which can't print Chinese listing titles
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="taobom", description="KiCad BOM -> Taobao purchase plan")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_parts = sub.add_parser("parts", help="show purchasable parts parsed from a BOM CSV")
    p_parts.add_argument("bom")

    p_mock = sub.add_parser("mock", help="generate fake shop listings for a BOM (pipeline test)")
    p_mock.add_argument("bom")
    p_mock.add_argument("-o", "--output", default="listings.json")

    p_plan = sub.add_parser("plan", help="solve shop consolidation and write the shopping list")
    p_plan.add_argument("bom")
    p_plan.add_argument("listings")
    p_plan.add_argument("-o", "--output", default="plan.md")
    p_plan.add_argument("--max-shops", type=int, default=5)
    p_plan.add_argument("--shop-penalty", type=float, default=8.0,
                        help="CNY-equivalent hassle cost per extra shop (default 8)")

    args = ap.parse_args(argv)
    parts = load_bom(args.bom)

    if args.cmd == "parts":
        for p in parts:
            print(f"{p.qty:>3} x {p.display:<40} {p.footprint}")
            print(f"      search: {' | '.join(p.search_terms)}")
        return 0

    if args.cmd == "mock":
        shops, listings = mock_listings(parts)
        save_listings(Path(args.output), shops, listings)
        print(f"{len(listings)} mock listings across {len(shops)} shops -> {args.output}")
        return 0

    if args.cmd == "plan":
        shops, listings = load_listings(Path(args.listings))
        try:
            plan = solve(parts, shops, listings,
                         max_shops=args.max_shops, shop_penalty=args.shop_penalty)
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
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
