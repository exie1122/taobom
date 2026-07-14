"""Render a Plan as a per-shop shopping checklist (markdown, WeChat-friendly)."""
from __future__ import annotations

from .models import Plan


def render_plan(plan: Plan, title: str = "购物清单 / Shopping plan") -> str:
    lines: list[str] = [f"# {title}", ""]
    by_shop = plan.by_shop()
    lines.append(
        f"**{len(by_shop)} shops · {sum(len(a) for a in by_shop.values())} items · "
        f"总计约 ¥{plan.total:.2f}**"
    )
    lines.append("")

    for n, (shop_id, assigns) in enumerate(
        sorted(by_shop.items(), key=lambda kv: -plan.shop_subtotal(kv[0])), start=1
    ):
        shop = plan.shops[shop_id]
        sub = plan.shop_subtotal(shop_id)
        ship = plan.shop_shipping(shop_id)
        lines.append(f"## 店铺 {n}: {shop.name}")
        meta = []
        if shop.url:
            meta.append(shop.url)
        if shop.ship_from:
            meta.append(f"发货地: {shop.ship_from}")
        if shop.rating:
            meta.append(f"评分 {shop.rating:.1f}")
        if meta:
            lines.append(" · ".join(meta))
        lines.append("")
        for a in sorted(assigns, key=lambda a: a.part.id):
            lines.append(f"- [ ] **{a.part.display}** × {a.part.qty}")
            lines.append(f"      {a.listing.title}")
            if a.listing.sku_note:
                lines.append(f"      规格选择: **{a.listing.sku_note}**")
            lot = (
                f"{a.lots} 件 (每件 {a.listing.lot_size} 个)"
                if a.listing.lot_size > 1
                else f"{a.lots} 个"
            )
            lines.append(f"      买 {lot} = ¥{a.cost:.2f}")
            if a.listing.url:
                lines.append(f"      {a.listing.url}")
            if a.listing.confidence < 0.8:
                lines.append(
                    f"      ⚠ 匹配置信度 {a.listing.confidence:.0%} — 请人工确认"
                )
        ship_note = f" + 运费 ¥{ship:.2f}" if ship else " · 包邮"
        lines.append("")
        lines.append(f"**小计 ¥{sub:.2f}{ship_note}**")
        lines.append("")

    if plan.unmatched:
        lines.append("## ⚠ 未找到货源 (needs manual sourcing)")
        lines.append("")
        for p in plan.unmatched:
            lines.append(f"- **{p.display}** × {p.qty} ({', '.join(p.refs)})")
            if p.search_terms:
                lines.append(f"      建议搜索: {' / '.join(p.search_terms)}")
        lines.append("")

    return "\n".join(lines)
