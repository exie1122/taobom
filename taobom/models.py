"""Core data models shared by the BOM parser, matcher, solver, and output."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Part:
    """One purchasable line item from the BOM (grouped by value+footprint)."""

    id: str                     # stable slug, e.g. "0.1uF_C0603"
    refs: list[str]             # expanded designators, e.g. ["C2", "C3", ...]
    value: str
    footprint: str
    qty: int
    mpn: str = ""
    package: str = ""           # extracted: 0603, 0805, SOT-23, ...
    search_terms: list[str] = field(default_factory=list)
    note: str = ""              # free-form buyer note (spares, per-board needs)
    lcsc: str = ""              # LCSC / JLC catalogue number, e.g. "C1591"

    @property
    def display(self) -> str:
        bits = [self.value]
        if self.package:
            bits.append(self.package)
        if self.mpn:
            bits.append(f"[{self.mpn}]")
        return " ".join(bits)


@dataclass
class Shop:
    id: str
    name: str
    url: str = ""
    ship_from: str = ""
    rating: float = 0.0         # 0-5, from shop page
    free_ship_over: float = 0.0  # CNY threshold for free domestic shipping, 0 = always free
    ship_fee: float = 0.0       # CNY, charged if subtotal below threshold


@dataclass
class Listing:
    """One candidate offer: a specific SKU in a specific shop for one Part."""

    part_id: str
    shop_id: str
    title: str                  # listing title as shown on Taobao
    url: str = ""
    price: float = 0.0          # CNY per lot
    lot_size: int = 1           # units per lot (e.g. 100 for a resistor pack)
    sku_note: str = ""          # exact variant to pick in the 规格 dropdown
    confidence: float = 1.0     # 0-1 match confidence (1.0 for mock/manual)
    # Optional signals the scraper can fill in when Taobao exposes them.
    sales: int = 0              # units sold, 0 = unknown
    stock: int = 0              # units in stock, 0 = unknown
    shop_url: str = ""          # shop front page
    source_query: str = ""      # search query that produced this listing
    verified: bool = False      # a human confirmed the variant/price/lot size
    match_reasons: list[str] = field(default_factory=list)  # why it matched

    def cost_for(self, qty: int) -> float:
        """Total CNY to obtain at least `qty` units."""
        lots = -(-qty // self.lot_size)  # ceil division
        return lots * self.price

    def lots_for(self, qty: int) -> int:
        return -(-qty // self.lot_size)


@dataclass
class Assignment:
    """Solver output: which listing fulfils which part."""

    part: Part
    listing: Listing
    lots: int
    cost: float


@dataclass
class Plan:
    """Full purchase plan: assignments grouped per shop + anything unmatched."""

    shops: dict[str, Shop]
    assignments: list[Assignment]
    unmatched: list[Part]
    # Candidate listings that lost to the chosen one, keyed by part id. Used for
    # the "if it is out of stock" fallback list in the rendered plan.
    alternatives: dict[str, list[Listing]] = field(default_factory=dict)
    # Shop-cap bookkeeping: requested_max == 0 means no cap was requested.
    requested_max_shops: int = 0
    min_shops_required: int = 0
    # Cheapest shop able to supply every matched part on its own, if any.
    single_shop: tuple[str, float] | None = None
    # "ilp" (optimal) or "greedy" (pulp/CBC unavailable).
    engine: str = "ilp"

    def by_shop(self) -> dict[str, list[Assignment]]:
        out: dict[str, list[Assignment]] = {}
        for a in self.assignments:
            out.setdefault(a.listing.shop_id, []).append(a)
        return out

    def shop_subtotal(self, shop_id: str) -> float:
        return sum(a.cost for a in self.assignments if a.listing.shop_id == shop_id)

    def shop_shipping(self, shop_id: str) -> float:
        """Ship fee is waived when the subtotal clears the shop's free-shipping threshold."""
        shop = self.shops[shop_id]
        if shop.ship_fee <= 0:
            return 0.0
        if shop.free_ship_over > 0 and self.shop_subtotal(shop_id) >= shop.free_ship_over:
            return 0.0
        return shop.ship_fee

    @property
    def cap_relaxed(self) -> bool:
        return bool(self.requested_max_shops) and self.min_shops_required > self.requested_max_shops

    @property
    def total(self) -> float:
        parts = sum(a.cost for a in self.assignments)
        ship = sum(self.shop_shipping(s) for s in self.by_shop())
        return parts + ship

    def savings_vs_single_shop(self) -> tuple[str, float] | None:
        """(shop_id, saving) when a one-shop plan exists and is more expensive."""
        if not self.single_shop:
            return None
        shop_id, cost = self.single_shop
        saving = cost - self.total
        return (shop_id, saving) if saving > 0.01 else None
