"""Interactive item-page verification for search-derived listings.

Taobao search prices often refer to the cheapest unrelated variant. This pass
keeps a human in control: it opens each candidate in the persisted logged-in
Chrome profile and records the exact variant, per-lot price, and lot size.
"""
from __future__ import annotations

from dataclasses import replace

from .models import Listing
from .scrape import _launch


def _positive_number(prompt: str, current: float, integer: bool = False):
    while True:
        raw = input(f"{prompt} [{current}]: ").strip()
        if not raw:
            return current
        try:
            value = int(raw) if integer else float(raw)
            if value <= 0:
                raise ValueError
            return value
        except ValueError:
            print("  Enter a positive number, or press Enter to keep the current value.")


def verify_listings(
    listings: list[Listing],
    part_id: str = "",
    uncertain_only: bool = False,
) -> list[Listing]:
    """Open candidates one by one and return the updated/filtered list."""
    eligible = [
        listing for listing in listings
        if (not part_id or listing.part_id == part_id)
        and (not uncertain_only or listing.confidence < 0.8 or not listing.verified)
    ]
    if not eligible:
        print("No listings match the verification filters.")
        return listings

    pw, ctx = _launch()
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    updated: list[Listing] = []
    try:
        for number, listing in enumerate(listings, start=1):
            if listing not in eligible:
                updated.append(listing)
                continue
            print(
                f"\n[{number}/{len(listings)}] {listing.part_id}\n"
                f"  {listing.title}\n"
                f"  search price ¥{listing.price:g} · confidence {listing.confidence:.0%}"
            )
            if listing.url:
                try:
                    page.goto(listing.url, wait_until="domcontentloaded")
                except Exception as exc:
                    print(f"  Could not load item page automatically: {exc}")
            action = input(
                "  [v] verify  [s] skip  [d] drop bad listing  [q] save and quit: "
            ).strip().lower() or "s"
            if action == "q":
                updated.append(listing)
                updated.extend(listings[number:])
                break
            if action == "d":
                continue
            if action != "v":
                updated.append(listing)
                continue

            sku = input(f"  Exact 规格/SKU [{listing.sku_note}]: ").strip()
            price = _positive_number("  Actual price per lot", listing.price)
            lot_size = _positive_number(
                "  Units received per lot", listing.lot_size, integer=True
            )
            updated.append(replace(
                listing,
                sku_note=sku or listing.sku_note,
                price=float(price),
                lot_size=int(lot_size),
                verified=True,
                match_reasons=[*listing.match_reasons, "item page manually verified"],
            ))
    finally:
        ctx.close()
        pw.stop()
    return updated
