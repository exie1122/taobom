"""Taobao search through a persistent Chrome profile."""
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
LOGIN_COOKIES = ("unb", "_nk_", "lgc")


def _launch():
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        channel="chrome",
        headless=False,
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
        for _ in range(300):
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
        sku_note = (
            d.get("skuName")
            or d.get("skuText")
            or d.get("saleProp")
            or ""
        )
        items.append({
            "title": _clean_title(title),
            "price": price_f,
            "item_id": str(item_id),
            "shop": str(shop).strip(),
            "location": d.get("procity") or d.get("area") or "",
            "sku_note": str(sku_note).strip() if isinstance(sku_note, (str, int)) else "",
        })
    seen, out = set(), []
    for it in items:
        key = it["item_id"] or it["title"]
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def _token_variants(tok: str) -> set[str]:
    out = {tok}
    m = re.fullmatch(r"([A-Z]*\d{3,})[A-Z0-9]{1,3}", tok)
    if m:
        out.add(m.group(1))
    return out


ATTRIBUTE_PATTERNS = {
    "voltage": re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*V\b", re.IGNORECASE),
    "current": re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*(?:UA|MA|A)\b", re.IGNORECASE),
    "pin count": re.compile(r"(?<!\d)\d+\s*(?:P|PIN)\b", re.IGNORECASE),
    "pitch": re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*MM\b", re.IGNORECASE),
    "dielectric": re.compile(r"\bX[578][Rr]\b", re.IGNORECASE),
}
PACKAGE_PATTERN = re.compile(
    r"\b(?:SOT-?23(?:-[56])?|SOT-?89|SOT-?223|SOD-?123|SOD-?323|"
    r"QFN-?\d+|LQFP-?\d+|TQFP-?\d+|TSSOP-?\d+|SSOP-?\d+|"
    r"SOIC-?\d+|SOP-?\d+|MSOP-?\d+|DIP-?\d+|BGA-?\d+|"
    r"0201|0402|0603|0805|1206|1210|2010|2512)\b",
    re.IGNORECASE,
)


def _norm_attr(value: str) -> str:
    normalized = re.sub(r"[\s-]+", "", value.upper())
    return re.sub(r"PIN$", "P", normalized)


def _match(part: Part, title: str) -> tuple[float, list[str]]:
    t = title.upper()
    reasons: list[str] = []
    is_discrete_resistor = bool(
        re.match(r"^[\d.]+[kKmMrR]?[\d]*(?:Ω|ohm)?\b", part.value)
        and re.search(r"(?:电阻|RESISTOR)", " ".join(part.search_terms), re.IGNORECASE)
    )
    if is_discrete_resistor and re.search(
        r"排阻|电阻阵列|网络电阻|0603\s*[*×X]\s*4|ARRAY", title, re.IGNORECASE
    ):
        return 0.0, ["resistor array is not a discrete resistor"]
    exact_mpn = bool(part.mpn and part.mpn.upper() in t)
    if exact_mpn:
        reasons.append("exact MPN")
    part_text = " ".join(
        [part.value, part.package, part.mpn, *part.search_terms]
    ).upper()
    for label, pattern in ATTRIBUTE_PATTERNS.items():
        expected = {_norm_attr(v) for v in pattern.findall(part_text)}
        observed = {_norm_attr(v) for v in pattern.findall(t)}
        if expected and observed:
            if expected.isdisjoint(observed):
                return 0.0, [
                    f"conflicting {label}: expected {sorted(expected)}, "
                    f"found {sorted(observed)}"
                ]
            reasons.append(f"{label} matches")

    expected_packages = {_norm_attr(v) for v in PACKAGE_PATTERN.findall(part.package)}
    observed_packages = {_norm_attr(v) for v in PACKAGE_PATTERN.findall(t)}
    if expected_packages and observed_packages:
        if expected_packages.isdisjoint(observed_packages):
            return 0.0, [
                f"conflicting package: expected {sorted(expected_packages)}, "
                f"found {sorted(observed_packages)}"
            ]
        reasons.append("package matches")

    if exact_mpn:
        return 1.0, reasons

    best = 0.0
    best_term = ""
    for term in part.search_terms:
        tokens = [tok for tok in re.split(r"\s+", term.upper()) if tok]
        if not tokens:
            continue
        hit = sum(1 for tok in tokens if any(v in t for v in _token_variants(tok)))
        score = hit / len(tokens)
        if score > best:
            best, best_term = score, term
    if best_term:
        reasons.append(f"search-term tokens {best:.0%}")
    confidence = min(0.98, 0.9 * best + 0.02 * len(reasons))
    return round(confidence, 2), reasons


def _score(part: Part, title: str) -> float:
    return _match(part, title)[0]


def _infer_lot_size(title: str) -> int:
    match = re.search(
        r"[\(（]\s*(?:发|每份|每件|一份)?\s*(\d{1,5})\s*"
        r"(?:只|个|片|PCS)\s*[\)）]",
        title,
        re.IGNORECASE,
    )
    return int(match.group(1)) if match else 1


def _shop_slug(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9一-鿿]+", "_", name).strip("_") or "unknown"
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"{base}_{digest}"


def scrape(
    parts: list[Part],
    raw_dir: Path,
    per_part: int = 20,
    min_confidence: float = 0.3,
    delay: float = 6.0,
    retries: int = 2,
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
            queries = list(dict.fromkeys(part.search_terms or [part.value]))
            raw_queries: list[dict] = []
            diagnostics = {
                "part_id": part.id, "queries": queries, "parsed": 0,
                "accepted": 0, "rejected": [],
            }
            candidates: dict[tuple[str, str], tuple[dict, float, str, list[str]]] = {}
            for query_index, query in enumerate(queries):
                query_items: list[dict] = []
                payloads: list[dict] = []
                for attempt in range(retries + 1):
                    captured.clear()
                    print(f"[{part.id}] searching: {query} "
                          f"(attempt {attempt + 1}/{retries + 1})")
                    page.goto(SEARCH_URL.format(q=quote(query)),
                              wait_until="domcontentloaded")
                    time.sleep(delay + random.uniform(0, 2))

                    if "验证" in page.title() or "captcha" in page.url.lower():
                        input("  Captcha — solve it in the window, then press Enter here...")
                        time.sleep(2)

                    payloads = list(captured)
                    for payload in payloads:
                        query_items.extend(_extract_items(payload))
                    if query_items:
                        break
                raw_queries.append({"query": query, "payloads": payloads})
                if not query_items:
                    (raw_dir / f"{part.id}.{query_index}.html").write_text(
                        page.content(), encoding="utf-8"
                    )
                    print(f"  no items parsed ({len(payloads)} mtop payloads dumped)")
                    continue

                diagnostics["parsed"] += len(query_items)
                for item in query_items:
                    confidence, reasons = _match(part, item["title"])
                    rejection = None
                    if not item["shop"]:
                        rejection = "missing shop"
                    elif confidence < min_confidence:
                        rejection = "; ".join(reasons) or f"confidence {confidence:.0%}"
                    if rejection:
                        diagnostics["rejected"].append({
                            "title": item["title"], "query": query, "reason": rejection,
                        })
                        continue
                    key = (item["item_id"] or item["title"], item["shop"])
                    previous = candidates.get(key)
                    candidate = (item, confidence, query, reasons)
                    if previous is None or (confidence, -item["price"]) > (
                        previous[1], -previous[0]["price"]
                    ):
                        candidates[key] = candidate

            (raw_dir / f"{part.id}.json").write_text(
                json.dumps(raw_queries, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            scored = list(candidates.values())
            prices = sorted(x[0]["price"] for x in scored)
            if prices:
                median = prices[len(prices) // 2]
                if median >= 5:
                    kept = []
                    for candidate in scored:
                        if candidate[1] >= 0.95 or candidate[0]["price"] >= 0.08 * median:
                            kept.append(candidate)
                        else:
                            diagnostics["rejected"].append({
                                "title": candidate[0]["title"],
                                "query": candidate[2],
                                "reason": "implausible placeholder price",
                            })
                    scored = kept
            scored.sort(key=lambda x: (-x[1], x[0]["price"]))
            diagnostics["accepted"] = min(len(scored), per_part)
            (raw_dir / f"{part.id}.diagnostics.json").write_text(
                json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            for it, conf, query, reasons in scored[:per_part]:
                sid = _shop_slug(it["shop"])
                shops.setdefault(sid, Shop(
                    id=sid, name=it["shop"], ship_from=it["location"],
                ))
                listings.append(Listing(
                    part_id=part.id, shop_id=sid, title=it["title"],
                    url=f"https://item.taobao.com/item.htm?id={it['item_id']}",
                    price=it["price"], lot_size=_infer_lot_size(it["title"]),
                    sku_note=it["sku_note"],
                    confidence=conf, source_query=query, match_reasons=reasons,
                    verified=False,
                ))
            print(f"  kept {min(len(scored), per_part)}/{diagnostics['parsed']} candidates")
        return list(shops.values()), listings
    finally:
        ctx.close()
        pw.stop()
