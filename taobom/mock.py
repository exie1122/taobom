"""Deterministic fake Taobao shops/listings so the pipeline runs end-to-end
before the real scraper exists. Prices are plausible CNY, not real."""
from __future__ import annotations

import hashlib
import re

from .models import Listing, Part, Shop

SHOPS = [
    Shop("jixin", "深圳集芯电子", "https://shop.example/jixin", "深圳", 4.9, 20.0, 6.0),
    Shop("youxin", "优信电子元器件", "https://shop.example/youxin", "深圳", 4.8, 0.0, 0.0),
    Shop("hangtai", "航泰电子", "https://shop.example/hangtai", "东莞", 4.7, 15.0, 5.0),
    Shop("xinbanfang", "芯板坊模块店", "https://shop.example/xinbanfang", "上海", 4.9, 0.0, 8.0),
    Shop("weishi", "威视传感器店", "https://shop.example/weishi", "深圳", 4.6, 0.0, 6.0),
]


def _jitter(key: str, base: float, spread: float = 0.3) -> float:
    """Deterministic per-(part,shop) price wobble so shops differ."""
    h = int(hashlib.md5(key.encode()).hexdigest()[:6], 16) / 0xFFFFFF
    return round(base * (1 + spread * (h - 0.5)), 2)


def _is_passive(p: Part) -> str | None:
    if re.fullmatch(r"[\d.]+[pnum]?F", p.value, re.IGNORECASE):
        return "cap"
    if re.fullmatch(r"[\d.]+[kKmMrR]?[\d]*(Ω|ohm)?", p.value):
        return "res"
    return None


def mock_listings(parts: list[Part]) -> tuple[list[Shop], list[Listing]]:
    listings: list[Listing] = []
    for p in parts:
        kind = _is_passive(p)
        if kind and p.package:
            kw = "贴片电容" if kind == "cap" else "贴片电阻"
            for shop in SHOPS[:3]:
                listings.append(Listing(
                    part_id=p.id, shop_id=shop.id,
                    title=f"{kw} {p.package} 全系列 一件20只起售",
                    url=f"{shop.url}/item/{p.id}",
                    price=_jitter(p.id + shop.id, 1.5), lot_size=20,
                    sku_note=f"{p.package} {p.value}",
                ))
        elif "XC6206" in p.value.upper():
            model = p.value.split()[0]
            for shop in SHOPS[:2]:
                listings.append(Listing(
                    part_id=p.id, shop_id=shop.id,
                    title=f"{model} SOT-23 LDO稳压 (10只)",
                    url=f"{shop.url}/item/{p.id}",
                    price=_jitter(p.id + shop.id, 2.8), lot_size=10,
                    sku_note=model,
                ))
        elif "CORE1106" in p.value.upper():
            shop = SHOPS[3]
            listings.append(Listing(
                part_id=p.id, shop_id=shop.id,
                title="Core1106 SG2002 核心板 RISC-V Linux 开发板",
                url=f"{shop.url}/item/{p.id}",
                price=95.0, lot_size=1, sku_note="Core1106-G3 邮票孔版",
            ))
        elif "SC035HGS" in p.value.upper():
            for shop in (SHOPS[3], SHOPS[4]):
                listings.append(Listing(
                    part_id=p.id, shop_id=shop.id,
                    title="SC035HGS 全局快门摄像头模组 24P FPC 0.5mm",
                    url=f"{shop.url}/item/{p.id}",
                    price=_jitter(p.id + shop.id, 28.0), lot_size=1,
                    sku_note="24P 同向 0.5间距",
                    confidence=0.7,
                ))
        # anything else: no listing -> exercises the unmatched path
    return SHOPS, listings
