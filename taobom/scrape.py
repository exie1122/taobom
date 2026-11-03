"""Standalone Taobao search scraper — no LLM in the loop.

Uses Playwright driving the *installed* Chrome (channel="chrome") with a
persistent profile in ~/.taobom/chrome_profile:

  python -m taobom login              one-time: QR-scan in the opened window
  python -m taobom scrape bom.csv ... every run after that is unattended

Search results arrive via Taobao's mtop H5 JSON API; we intercept those
responses rather than scraping the DOM, and dump every raw payload to
raw/<part_id>.json so extraction bugs can be fixed offline without re-hitting
the site (``--reuse-raw`` replays them). Requests are paced human-slow on
purpose.
"""
from __future__ import annotations

import hashlib
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
# unb (numeric user id) and _nk_/lgc (nickname) only exist on a real login;
# cookie2 also appears on anonymous sessions, so it must not count
LOGIN_COOKIES = ("unb", "_nk_", "lgc")


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


def _as_int(value) -> int:
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip().lower()
    if not text:
        return 0
    m = re.match(r"([\d.]+)\s*(万)?", text)
    if not m:
        return 0
    try:
        number = float(m.group(1))
    except ValueError:
        return 0
    if m.group(2):
        number *= 10_000
    return max(0, int(number))


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
            "shop_url": str(d.get("shopLink") or d.get("shopUrl") or "").strip(),
            "location": d.get("procity") or d.get("area") or "",
            "sales": _as_int(d.get("realSales") or d.get("sold") or d.get("totalSold")),
            "stock": _as_int(d.get("stock") or d.get("quantity")),
        })
    # dedupe by item_id/title
    seen, out = set(), []
    for it in items:
        key = it["item_id"] or it["title"]
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def _token_variants(tok: str) -> set[str]:
    """'CORE1106G3' also matches titles saying just 'Core1106' — vendors drop
    short trailing revision suffixes after the numeric body."""
    out = {tok}
    m = re.fullmatch(r"([A-Z]*\d{3,})[A-Z0-9]{1,3}", tok)
    if m:
        out.add(m.group(1))
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
        hit = sum(1 for tok in tokens if any(v in t for v in _token_variants(tok)))
        best = max(best, hit / len(tokens))
    return round(0.9 * best, 2)  # never claim 1.0 without an MPN hit


LOT_PATTERNS = (
    # "10只装", "100个/包", "50片起售"
    re.compile(r"(\d{1,4})\s*(?:只|个|片|支|pcs)\s*(?:装|/包|/件|起售|起)"),
    # "（10只）", "(50片)"
    re.compile(r"[（(]\s*(\d{1,4})\s*(?:只|个|片|支|pcs)\s*[)）]"),
    # "一包100只"
    re.compile(r"(?:一包|每包|每份)\s*(\d{1,4})\s*(?:只|个|片|支)"),
)


def infer_lot_size(title: str) -> int:
    """Best-effort pack size from a listing title; 1 when unsure.

    Taobao search pages do not expose the 规格 variant, so this only trusts
    explicit "10只装"-style wording. Getting it wrong either way skews the
    estimated total, hence the conservative patterns.
    """
    for pattern in LOT_PATTERNS:
        m = pattern.search(title)
        if m:
            size = int(m.group(1))
            if 1 < size <= 1000:
                return size
    return 1


def _shop_slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "_", name).strip("_")
    return slug or "unknown"


class ShopRegistry:
    """Shop ids that stay stable and never collide after slug normalisation."""

    def __init__(self, shops: list[Shop] | None = None) -> None:
        self._by_id: dict[str, Shop] = {s.id: s for s in (shops or [])}

    def add(self, name: str, location: str = "", url: str = "") -> Shop:
        name = name.strip() or "unknown"
        slug = _shop_slug(name)
        shop = self._by_id.get(slug)
        if shop is not None and shop.name != name:
            # "foo bar" and "foo-bar" normalise identically; keep them apart.
            digest = hashlib.md5(name.encode("utf-8")).hexdigest()[:6]
            slug = f"{slug}-{digest}"
        shop = self._by_id.get(slug)
        if shop is None:
            shop = Shop(id=slug, name=name, url=url, ship_from=location)
            self._by_id[slug] = shop
        else:
            if location and not shop.ship_from:
                shop.ship_from = location
            if url and not shop.url:
                shop.url = url
        return shop

    @property
    def shops(self) -> list[Shop]:
        return list(self._by_id.values())


def _plausible(items: list[dict], part: Part,
               min_confidence: float = 0.3) -> list[tuple[dict, float]]:
    scored = [(it, _score(part, it["title"])) for it in items]
    scored = [x for x in scored if x[1] >= min_confidence and x[0]["shop"]]
    # drop ¥0.01-style placeholder listings: far below the median price
    # of plausible candidates for the same part
    prices = sorted(x[0]["price"] for x in scored)
    if prices:
        median = prices[len(prices) // 2]
        if median >= 5:
            scored = [x for x in scored if x[0]["price"] >= 0.08 * median]
    scored.sort(key=lambda x: (-x[1], x[0]["price"]))
    return scored


def listings_for_part(
    part: Part,
    payloads: list[dict],
    registry: ShopRegistry,
    per_part: int = 8,
    min_confidence: float = 0.3,
    query: str = "",
) -> list[Listing]:
    """Turn captured mtop payloads into candidates for one part."""
    items: list[dict] = []
    for payload in payloads:
        items.extend(_extract_items(payload))
    if not items:
        return []

    scored = [(it, conf) for it, conf in
              _plausible(items, part, min_confidence) if conf >= min_confidence]
    out: list[Listing] = []
    for it, conf in scored[:per_part]:
        shop = registry.add(it["shop"], it["location"], it.get("shop_url", ""))
        out.append(Listing(
            part_id=part.id,
            shop_id=shop.id,
            title=it["title"],
            url=(f"https://item.taobao.com/item.htm?id={it['item_id']}"
                 if it["item_id"] else ""),
            price=it["price"],
            lot_size=infer_lot_size(it["title"]),
            confidence=conf,
            sales=it.get("sales", 0),
            stock=it.get("stock", 0),
            shop_url=shop.url,
            source_query=query,
        ))
    return out


def _raw_path(raw_dir: Path, part: Part) -> Path:
    return raw_dir / f"{part.id}.json"


def _read_raw(path: Path) -> list[dict] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) and data else None


def _eta(done: int, total: int, started: float) -> str:
    if done <= 0:
        return ""
    remaining = (time.monotonic() - started) / done * (total - done)
    if remaining < 60:
        return f"{remaining:.0f}s"
    return f"{remaining / 60:.0f}m"


def scrape(
    parts: list[Part],
    raw_dir: Path,
    per_part: int = 8,
    min_confidence: float = 0.3,
    delay: float = 6.0,
    reuse_raw: bool = False,
    only: list[str] | None = None,
    on_snapshot=None,
    shops: list[Shop] | None = None,
) -> tuple[list[Shop], list[Listing]]:
    """Search Taobao for each part and return candidate shops/listings.

    ``reuse_raw`` replays previously dumped payloads instead of hitting the
    network, so a partial run can be resumed or re-parsed offline.
    ``on_snapshot(done, total, shops, listings)`` is called after every part so
    the CLI can save incrementally instead of losing a long run.
    ``shops`` seeds the registry with shops already present in an existing
    listings file, keeping ids stable across ``--only`` runs.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    if only:
        wanted = set(only)
        unknown = sorted(wanted - {p.id for p in parts})
        if unknown:
            raise ValueError("--only refers to unknown part ids: " + ", ".join(unknown))
        parts = [p for p in parts if p.id in wanted]
    if not parts:
        return [], []

    registry = ShopRegistry(shops)
    all_listings: list[Listing] = []
    started = time.monotonic()
    total = len(parts)

    def emit(done: int) -> None:
        if on_snapshot:
            on_snapshot(done, total, registry.shops, all_listings)

    cached = {p.id: _read_raw(_raw_path(raw_dir, p)) for p in parts} if reuse_raw else {}
    needs_browser = any(cached.get(p.id) is None for p in parts)

    pw = ctx = page = None
    captured: list[dict] = []
    try:
        if needs_browser:
            pw, ctx = _launch()
            if not _logged_in(ctx):
                print("Not logged in — run `python -m taobom login` first.")
                return [], []
            page = ctx.new_page()

            def on_response(resp):
                if any(m in resp.url for m in MTOP_MARKERS):
                    try:
                        captured.append(json.loads(_strip_jsonp(resp.text())))
                    except Exception:
                        pass

            page.on("response", on_response)

        for index, part in enumerate(parts, start=1):
            query = part.search_terms[0] if part.search_terms else part.value
            elapsed = _eta(index - 1, total, started)
            suffix = f"  (eta {elapsed})" if elapsed else ""
            payloads = cached.get(part.id)
            if payloads is not None:
                print(f"[{index}/{total}] {part.id}: using cached raw payloads{suffix}")
            elif not query:
                print(f"[{index}/{total}] {part.id}: no search terms, skipped")
                continue
            else:
                captured.clear()
                print(f"[{index}/{total}] {part.id} searching: {query}{suffix}")
                page.goto(SEARCH_URL.format(q=quote(query)), wait_until="domcontentloaded")
                time.sleep(delay + random.uniform(0, 2))

                if "验证" in page.title() or "captcha" in page.url.lower():
                    input("  Captcha — solve it in the window, then press Enter here...")
                    time.sleep(2)

                _raw_path(raw_dir, part).write_text(
                    json.dumps(captured, ensure_ascii=False, indent=1), encoding="utf-8"
                )
                payloads = list(captured)
                if not any(_extract_items(p) for p in payloads):
                    # fallback so extraction bugs can be debugged offline
                    (raw_dir / f"{part.id}.html").write_text(
                        page.content(), encoding="utf-8")
                    print(f"  no items parsed ({len(payloads)} mtop payloads dumped)")

            got = listings_for_part(part, payloads, registry, per_part=per_part,
                                    min_confidence=min_confidence, query=query)
            all_listings.extend(got)
            print(f"  kept {len(got)} candidates")
            emit(index)
        return registry.shops, all_listings
    finally:
        if ctx is not None:
            ctx.close()
        if pw is not None:
            pw.stop()
