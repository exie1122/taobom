"""Standalone Taobao search scraper — no LLM in the loop.

Uses Playwright driving the *installed* Chrome (channel="chrome") with a
persistent profile in ~/.taobom/chrome_profile:

  python -m taobom login              one-time: QR-scan in the opened window
  python -m taobom scrape bom.csv ... every run after that is unattended

Search results arrive via Taobao's mtop H5 JSON API; we intercept those
responses rather than scraping the DOM, and dump every raw payload to
raw/<part_id>.json so extraction bugs can be fixed offline without
re-hitting the site. Requests are paced human-slow on purpose.
"""
from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path
from urllib.parse import quote

from .models import Listing, Part, Shop

PROFILE_DIR = Path.home() / ".taobom" / "chrome_profile"
SEARCH_URL = "https://s.taobao.com/search?q={q}"
MTOP_MARKERS = ("mtop.relationrecommend", "mtop.taobao.wsearch")
LOGIN_COOKIES = ("_nk_", "lgc", "cookie2")


def _launch():
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        channel="chrome",
        headless=False,  # headful: far less bot-flagged, and captchas are solvable
        viewport=None,
        args=["--disable-blink-features=AutomationControlled"],
    )
    return pw, ctx


def _logged_in(ctx) -> bool:
    names = {c["name"] for c in ctx.cookies("https://www.taobao.com")}
    return any(n in names for n in LOGIN_COOKIES)


def login() -> int:
    pw, ctx = _launch()
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.taobao.com")
        print("Log into Taobao in the opened window (QR scan with the app).")
        print("Waiting for login", end="", flush=True)
        for _ in range(300):  # up to 10 min
            if _logged_in(ctx):
                print("\nLogged in. Session saved to", PROFILE_DIR)
                return 0
            time.sleep(2)
            print(".", end="", flush=True)
        print("\nTimed out waiting for login.")
        return 1
    finally:
        ctx.close()
        pw.stop()


def _strip_jsonp(text: str) -> str:
    m = re.match(r"\s*mtopjsonp\w*\((.*)\)\s*$", text, re.DOTALL)
    return m.group(1) if m else text


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _clean_title(t: str) -> str:
    return re.sub(r"<[^>]+>", "", t).strip()


def _extract_items(payload: dict) -> list[dict]:
    """Pull item-looking dicts out of an mtop response, schema-defensively."""
    items = []
    for d in _walk(payload):
        title = d.get("title") or d.get("raw_title")
        price = d.get("price") or (d.get("priceShow") or {}).get("price")
        if not (isinstance(title, str) and price):
            continue
        try:
            price_f = float(str(price).replace("¥", "").replace(",", ""))
        except ValueError:
            continue
        item_id = d.get("itemId") or d.get("nid") or d.get("item_id") or ""
        shop = (
            d.get("nick")
            or (d.get("shopInfo") or {}).get("title")
            or d.get("shopName")
            or ""
        )
        items.append({
            "title": _clean_title(title),
            "price": price_f,
            "item_id": str(item_id),
            "shop": str(shop).strip(),
            "location": d.get("procity") or d.get("area") or "",
        })
    # dedupe by item_id/title
    seen, out = set(), []
    for it in items:
        key = it["item_id"] or it["title"]
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def _score(part: Part, title: str) -> float:
    """Cheap lexical match confidence, 0..1. No LLM: MPN hit is decisive,
    otherwise fraction of search-term tokens present in the title."""
    t = title.upper()
    if part.mpn and part.mpn.upper() in t:
        return 1.0
    best = 0.0
    for term in part.search_terms:
        tokens = [tok for tok in re.split(r"\s+", term.upper()) if tok]
        if not tokens:
            continue
        hit = sum(1 for tok in tokens if tok in t) / len(tokens)
        best = max(best, hit)
    return round(0.9 * best, 2)  # never claim 1.0 without an MPN hit


def _shop_slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9一-鿿]+", "_", name).strip("_") or "unknown"


def scrape(
    parts: list[Part],
    raw_dir: Path,
    per_part: int = 8,
    min_confidence: float = 0.3,
    delay: float = 6.0,
) -> tuple[list[Shop], list[Listing]]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    pw, ctx = _launch()
    shops: dict[str, Shop] = {}
    listings: list[Listing] = []
    try:
        if not _logged_in(ctx):
            print("Not logged in — run `python -m taobom login` first.")
            return [], []
        page = ctx.new_page()
        captured: list[dict] = []

        def on_response(resp):
            if any(m in resp.url for m in MTOP_MARKERS):
                try:
                    captured.append(json.loads(_strip_jsonp(resp.text())))
                except Exception:
                    pass

        page.on("response", on_response)

        for part in parts:
            query = part.search_terms[0] if part.search_terms else part.value
            captured.clear()
            print(f"[{part.id}] searching: {query}")
            page.goto(SEARCH_URL.format(q=quote(query)), wait_until="domcontentloaded")
            time.sleep(delay + random.uniform(0, 2))

            if "验证" in page.title() or "captcha" in page.url.lower():
                input("  Captcha — solve it in the window, then press Enter here...")
                time.sleep(2)

            (raw_dir / f"{part.id}.json").write_text(
                json.dumps(captured, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            items = []
            for payload in captured:
                items.extend(_extract_items(payload))
            if not items:
                # fallback so we can debug extraction offline
                (raw_dir / f"{part.id}.html").write_text(page.content(), encoding="utf-8")
                print(f"  no items parsed ({len(captured)} mtop payloads dumped)")
                continue

            scored = [(it, _score(part, it["title"])) for it in items]
            scored = [x for x in scored if x[1] >= min_confidence and x[0]["shop"]]
            scored.sort(key=lambda x: (-x[1], x[0]["price"]))
            for it, conf in scored[:per_part]:
                sid = _shop_slug(it["shop"])
                shops.setdefault(sid, Shop(
                    id=sid, name=it["shop"], ship_from=it["location"],
                ))
                listings.append(Listing(
                    part_id=part.id, shop_id=sid, title=it["title"],
                    url=f"https://item.taobao.com/item.htm?id={it['item_id']}",
                    price=it["price"], lot_size=1, confidence=conf,
                ))
            print(f"  kept {min(len(scored), per_part)}/{len(items)} candidates")
        return list(shops.values()), listings
    finally:
        ctx.close()
        pw.stop()
