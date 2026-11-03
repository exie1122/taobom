"""Render a Plan as a per-shop shopping checklist.

Markdown is the default; HTML is a self-contained mobile page with Taobao app
deep links, and text is a plain block that pastes cleanly into WeChat.
"""
from __future__ import annotations

import dataclasses
import html
from urllib.parse import urlparse

from .models import Assignment, Listing, Plan

DEFAULT_TITLE = "购物清单 / Shopping plan"


def render(plan: Plan, fmt: str = "md", title: str = DEFAULT_TITLE) -> str:
    fmt = (fmt or "md").lower()
    if fmt in ("md", "markdown"):
        return render_plan(plan, title)
    if fmt == "html":
        return render_html(plan, title)
    if fmt in ("txt", "text"):
        return render_text(plan, title)
    if fmt == "json":
        import json
        return json.dumps(plan_to_dict(plan), ensure_ascii=False, indent=2)
    raise ValueError(f"unknown output format '{fmt}' (use md, html, text, or json)")


def _header(plan: Plan) -> list[str]:
    by_shop = plan.by_shop()
    lines = [
        f"**{len(by_shop)} shops · {sum(len(a) for a in by_shop.values())} items · "
        f"总计约 ¥{plan.total:.2f}**"
    ]
    warnings: list[str] = []
    if plan.cap_relaxed:
        warnings.append(
            f"> ⚠ 覆盖全部元件至少需要 {plan.min_shops_required} 家店铺，"
            f"超过 --max-shops {plan.requested_max_shops}。清单已按最少店铺数生成。"
        )
    saving = plan.savings_vs_single_shop()
    if saving:
        shop_id, amount = saving
        shop = plan.shops.get(shop_id)
        name = shop.name if shop else shop_id
        warnings.append(
            f"> 单店最优方案 ({name}) 约 ¥{plan.single_shop[1]:.2f}；"
            f"当前方案省 ¥{amount:.2f}，多 {len(by_shop) - 1} 个订单。"
        )
    if warnings:
        lines.append("")
        lines.extend(warnings)
    return lines + [
        "",
        "价格为搜索页起价, 实际以商品页所选规格为准。",
        "",
    ]


def _item_lines(a: Assignment) -> list[str]:
    lines = [f"- [ ] **{a.part.display}** × {a.part.qty}"]
    if a.part.lcsc:
        lines.append(f"      LCSC: {a.part.lcsc}")
    if a.part.note:
        lines.append(f"      备注: {a.part.note}")
    lines.append(f"      {a.listing.title}")
    if a.listing.sku_note:
        lines.append(f"      规格选择: **{a.listing.sku_note}**")
    lot = (
        f"{a.lots} 件 (每件 {a.listing.lot_size} 个)"
        if a.listing.lot_size > 1
        else f"{a.lots} 个"
    )
    lines.append(f"      买 {lot} = ¥{a.cost:.2f}")
    if a.listing.sales:
        lines.append(f"      已售 {a.listing.sales}")
    if a.listing.url:
        lines.append(f"      {a.listing.url}")
    if a.listing.verified:
        lines.append("      ✓ 已核对规格与价格")
    if a.listing.confidence < 0.8:
        lines.append(f"      ⚠ 匹配置信度 {a.listing.confidence:.0%} — 请人工确认")
    return lines


def render_plan(plan: Plan, title: str = DEFAULT_TITLE) -> str:
    lines: list[str] = [f"# {title}", ""]
    lines.extend(_header(plan))

    for n, (shop_id, assigns) in enumerate(
        sorted(plan.by_shop().items(), key=lambda kv: -plan.shop_subtotal(kv[0])), start=1
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
            lines.extend(_item_lines(a))
        ship_note = f" + 运费 ¥{ship:.2f}" if ship else " · 包邮"
        lines.append("")
        lines.append(f"**小计 ¥{sub:.2f}{ship_note}**")
        lines.append("")

    if plan.unmatched:
        lines.append("## ⚠ 未找到货源 (needs manual sourcing)")
        lines.append("")
        for p in plan.unmatched:
            lines.append(f"- **{p.display}** × {p.qty} ({', '.join(p.refs)})")
            if p.lcsc:
                lines.append(f"      LCSC: {p.lcsc}")
            if p.search_terms:
                lines.append(f"      建议搜索: {' / '.join(p.search_terms)}")
        lines.append("")

    alternatives = [
        (a.part, plan.alternatives.get(a.part.id, []))
        for a in plan.assignments
        if plan.alternatives.get(a.part.id)
    ]
    if alternatives:
        lines.append("## 备选货源 (if the chosen listing is out of stock)")
        lines.append("")
        for part, offers in sorted(alternatives, key=lambda x: x[0].id):
            lines.append(f"- **{part.display}** × {part.qty}")
            for lst in offers:
                shop = plan.shops.get(lst.shop_id)
                name = shop.name if shop else lst.shop_id
                bits = [f"¥{lst.price:.2f}", f"置信度 {lst.confidence:.0%}"]
                if lst.lot_size > 1:
                    bits.append(f"每件 {lst.lot_size} 个")
                lines.append(f"      {' · '.join(bits)} · {name}")
                if lst.url:
                    lines.append(f"      {lst.url}")
        lines.append("")
    return "\n".join(lines)


def _item_text(a: Assignment) -> list[str]:
    if a.listing.lot_size > 1:
        amount = f"{a.lots} 件 (每件 {a.listing.lot_size} 个)"
    else:
        amount = f"{a.lots} 个"
    head = f"  [ ] {a.part.display} x {a.part.qty}  ->  {amount} = ¥{a.cost:.2f}"
    lines = [head]
    if a.part.lcsc:
        lines.append(f"      LCSC {a.part.lcsc}")
    if a.part.note:
        lines.append(f"      备注 {a.part.note}")
    lines.append(f"      {a.listing.title}")
    if a.listing.sku_note:
        lines.append(f"      规格 {a.listing.sku_note}")
    if a.listing.url:
        lines.append(f"      {a.listing.url}")
    if a.listing.confidence < 0.8:
        lines.append(f"      ! 置信度 {a.listing.confidence:.0%} 请人工确认")
    return lines


def render_text(plan: Plan, title: str = DEFAULT_TITLE) -> str:
    """Plain text with no markdown syntax, for pasting into WeChat."""
    by_shop = plan.by_shop()
    lines = [title, "=" * 24,
             f"{len(by_shop)} shops / {len(plan.assignments)} items / 约 ¥{plan.total:.2f}"]
    if plan.cap_relaxed:
        lines.append(
            f"! 至少需要 {plan.min_shops_required} 家店铺 "
            f"(--max-shops {plan.requested_max_shops})"
        )
    saving = plan.savings_vs_single_shop()
    if saving:
        shop_id, amount = saving
        shop = plan.shops.get(shop_id)
        name = shop.name if shop else shop_id
        lines.append(f"单店最优 ({name}) ¥{plan.single_shop[1]:.2f}, 本方案省 ¥{amount:.2f}")
    lines.append("价格为搜索页起价, 以商品页规格为准.")
    lines.append("")

    for n, (shop_id, assigns) in enumerate(
        sorted(by_shop.items(), key=lambda kv: -plan.shop_subtotal(kv[0])), start=1
    ):
        shop = plan.shops[shop_id]
        ship = plan.shop_shipping(shop_id)
        lines.append(f"-- {n}. {shop.name} " + "-" * 6)
        meta = " | ".join(x for x in (shop.url, shop.ship_from) if x)
        if meta:
            lines.append(f"   {meta}")
        for a in sorted(assigns, key=lambda a: a.part.id):
            lines.extend(_item_text(a))
        ship_note = f" + 运费 ¥{ship:.2f}" if ship else " 包邮"
        lines.append(f"  小计 ¥{plan.shop_subtotal(shop_id):.2f}{ship_note}")
        lines.append("")

    if plan.unmatched:
        lines.append("-- 未找到货源 --")
        for p in plan.unmatched:
            lines.append(f"  {p.display} x {p.qty} 搜索: {' / '.join(p.search_terms)}")
        lines.append("")
    return "\n".join(lines)


def _app_link(url: str) -> str:
    """taobao:// deep link so a link opens in the app instead of the browser."""
    p = urlparse(url)
    if not p.netloc or not (p.netloc.endswith("taobao.com") or p.netloc.endswith("tmall.com")):
        return ""
    query = f"?{p.query}" if p.query else ""
    return f"taobao://{p.netloc}{p.path}{query}"


def _money(value: float) -> str:
    return f"¥{value:.2f}"


def render_html(plan: Plan, title: str = DEFAULT_TITLE) -> str:
    """Self-contained mobile page: tappable links, checkboxes, app deep links."""
    e = html.escape
    by_shop = plan.by_shop()
    out: list[str] = [
        "<!doctype html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{e(title)}</title>",
        """<style>
:root{color-scheme:light dark}
body{font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;margin:0;padding:16px 14px 60px;max-width:760px;margin-inline:auto}
h1{font-size:1.35rem;margin:0 0 .25rem}
h2{font-size:1.1rem;margin:1.6rem 0 .2rem}
.meta{opacity:.7;font-size:.9rem;margin:0 0 1rem}
.warn{background:#fff4e5;border-left:4px solid #e08b00;padding:.6rem .8rem;border-radius:6px;font-size:.92rem}
.save{background:#eef7ee;border-left:4px solid #2e7d32;padding:.6rem .8rem;border-radius:6px;font-size:.92rem}
.shopurl{word-break:break-all;font-size:.85rem;opacity:.75}
ul{list-style:none;padding:0;margin:.4rem 0}
li{border:1px solid rgba(128,128,128,.28);border-radius:10px;padding:.6rem .7rem;margin:.5rem 0}
li.done{border-color:#2e7d32;background:rgba(46,125,50,.07)}
label{display:block;font-weight:600}
.part{font-weight:600}
.title{display:block;margin:.35rem 0 .25rem;font-size:.95rem;opacity:.9}
.pick{font-size:.92rem}
.pick b{background:rgba(255,196,0,.28);padding:0 .2rem;border-radius:3px}
.links{margin-top:.45rem;display:flex;gap:.6rem;flex-wrap:wrap}
.links a{font-size:.9rem;text-decoration:none;border:1px solid rgba(128,128,128,.4);border-radius:999px;padding:.2rem .7rem}
.links a.primary{border-color:#c2410c;color:#c2410c}
.warnflag{color:#b45309;font-size:.85rem;display:block;margin-top:.3rem}
.okflag{color:#2e7d32;font-size:.85rem;display:block;margin-top:.3rem}
footer{margin-top:2rem;font-size:.85rem;opacity:.65}
</style></head><body>""",
        f"<h1>{e(title)}</h1>",
        f'<p class="meta">{len(by_shop)} shops · {len(plan.assignments)} items · '
        f"总计约 {_money(plan.total)}</p>",
    ]
    if plan.cap_relaxed:
        out.append(
            f'<p class="warn">覆盖全部元件至少需要 {plan.min_shops_required} 家店铺'
            f"（--max-shops {plan.requested_max_shops}）。清单按最少店铺数生成。</p>"
        )
    saving = plan.savings_vs_single_shop()
    if saving:
        shop_id, amount = saving
        shop = plan.shops.get(shop_id)
        name = e(shop.name if shop else shop_id)
        out.append(
            f'<p class="save">单店最优方案（{name}）约 {_money(plan.single_shop[1])}；'
            f"本方案省 {_money(amount)}。</p>"
        )

    index = 0
    for n, (shop_id, assigns) in enumerate(
        sorted(by_shop.items(), key=lambda kv: -plan.shop_subtotal(kv[0])), start=1
    ):
        shop = plan.shops[shop_id]
        out.append(f"<h2>店铺 {n}: {e(shop.name)}</h2>")
        meta = " · ".join(
            x for x in (f"发货地 {e(shop.ship_from)}" if shop.ship_from else "",
                        f"评分 {shop.rating:.1f}" if shop.rating else "") if x
        )
        if shop.url:
            meta = (meta + " · " if meta else "") + f'<span class="shopurl">{e(shop.url)}</span>'
        if meta:
            out.append(f'<p class="meta">{meta}</p>')
        out.append("<ul>")
        for a in sorted(assigns, key=lambda a: a.part.id):
            index += 1
            lst = a.listing
            out.append(f'<li data-i="{index}"><label><input type="checkbox" data-k="{index}"> ')
            out.append(f'<span class="part">{e(a.part.display)} × {a.part.qty}</span></label>')
            out.append(f'<span class="title">{e(lst.title)}</span>')
            if lst.sku_note:
                out.append(f'<span class="pick">规格: <b>{e(lst.sku_note)}</b></span><br>')
            lots = (f"{a.lots} 件 (每件 {lst.lot_size} 个)"
                    if lst.lot_size > 1 else f"{a.lots} 个")
            out.append(f'<span class="pick">买 {lots} = {_money(a.cost)}</span>')
            if lst.sales:
                out.append(f'<span class="pick"> · 已售 {lst.sales}</span>')
            if a.part.note:
                out.append(f'<span class="title">备注: {e(a.part.note)}</span>')
            if lst.verified:
                out.append('<span class="okflag">✓ 已核对规格与价格</span>')
            if lst.confidence < 0.8:
                out.append(
                    f'<span class="warnflag">⚠ 匹配置信度 {lst.confidence:.0%} — 请人工确认</span>'
                )
            if lst.url:
                out.append('<span class="links">')
                app = _app_link(lst.url)
                if app:
                    out.append(f'<a class="primary" href="{e(app)}">淘宝App打开</a>')
                out.append(
                    f'<a href="{e(lst.url)}" rel="noreferrer">{e("网页" if app else "打开商品")}</a>'
                )
                out.append("</span>")
            out.append("</li>")
        out.append("</ul>")
        ship = plan.shop_shipping(shop_id)
        ship_note = f" + 运费 {_money(ship)}" if ship else " · 包邮"
        out.append(
            f'<p class="meta">小计 {_money(plan.shop_subtotal(shop_id))}{ship_note}</p>'
        )

    if plan.unmatched:
        out.append("<h2>⚠ 未找到货源</h2><ul>")
        for p in plan.unmatched:
            out.append(
                f'<li><span class="part">{e(p.display)} × {p.qty}</span>'
                f'<span class="title">建议搜索: {e(" / ".join(p.search_terms))}</span></li>'
            )
        out.append("</ul>")

    if plan.alternatives:
        out.append("<h2>备选货源</h2><ul>")
        for a in sorted(plan.assignments, key=lambda a: a.part.id):
            offers = plan.alternatives.get(a.part.id)
            if not offers:
                continue
            out.append(f'<li><span class="part">{e(a.part.display)} × {a.part.qty}</span>')
            for lst in offers:
                shop = plan.shops.get(lst.shop_id)
                name = e(shop.name if shop else lst.shop_id)
                link = (f' · <a href="{e(lst.url)}" rel="noreferrer">{e(lst.url)}</a>'
                        if lst.url else "")
                out.append(
                    f'<span class="title">{_money(lst.price)} · 置信度 '
                    f"{lst.confidence:.0%} · {name}{link}</span>"
                )
            out.append("</li>")
        out.append("</ul>")

    out.append("<footer>价格为搜索页起价，实际以下单时规格为准。</footer>")
    out.append(
        """<script>
for (const box of document.querySelectorAll('input[type=checkbox]')) {
  const key = 'taobom:' + location.pathname + ':' + box.dataset.k;
  box.checked = localStorage.getItem(key) === '1';
  box.closest('li').classList.toggle('done', box.checked);
  box.addEventListener('change', () => {
    localStorage.setItem(key, box.checked ? '1' : '0');
    box.closest('li').classList.toggle('done', box.checked);
  });
}
</script></body></html>"""
    )
    return "\n".join(out)


def _listing_dict(plan: Plan, lst: Listing) -> dict:
    shop = plan.shops.get(lst.shop_id)
    data = dataclasses.asdict(lst)
    data["shop_name"] = shop.name if shop else lst.shop_id
    return data


def plan_to_dict(plan: Plan) -> dict:
    by_shop = plan.by_shop()
    return {
        "total": round(plan.total, 2),
        "shop_count": len(by_shop),
        "item_count": len(plan.assignments),
        "requested_max_shops": plan.requested_max_shops,
        "min_shops_required": plan.min_shops_required,
        "cap_relaxed": plan.cap_relaxed,
        "engine": plan.engine,
        "single_shop": (
            {"shop_id": plan.single_shop[0],
             "shop_name": plan.shops[plan.single_shop[0]].name
             if plan.single_shop[0] in plan.shops else plan.single_shop[0],
             "total": round(plan.single_shop[1], 2)}
            if plan.single_shop else None
        ),
        "shops": [
            {
                "id": sid,
                "name": plan.shops[sid].name,
                "url": plan.shops[sid].url,
                "subtotal": round(plan.shop_subtotal(sid), 2),
                "shipping": round(plan.shop_shipping(sid), 2),
            }
            for sid in by_shop
        ],
        "items": [
            {
                "part_id": a.part.id,
                "value": a.part.value,
                "package": a.part.package,
                "mpn": a.part.mpn,
                "lcsc": a.part.lcsc,
                "refs": list(a.part.refs),
                "qty": a.part.qty,
                "shop_id": a.listing.shop_id,
                "shop_name": plan.shops[a.listing.shop_id].name,
                "title": a.listing.title,
                "url": a.listing.url,
                "sku_note": a.listing.sku_note,
                "lots": a.lots,
                "lot_size": a.listing.lot_size,
                "cost": round(a.cost, 2),
                "confidence": a.listing.confidence,
                "verified": a.listing.verified,
            }
            for a in sorted(plan.assignments, key=lambda a: a.part.id)
        ],
        "unmatched": [
            {"part_id": p.id, "value": p.value, "package": p.package,
             "mpn": p.mpn, "lcsc": p.lcsc, "refs": list(p.refs), "qty": p.qty,
             "search_terms": list(p.search_terms)}
            for p in plan.unmatched
        ],
        "alternatives": {
            pid: [_listing_dict(plan, lst) for lst in offers]
            for pid, offers in plan.alternatives.items()
        },
    }
