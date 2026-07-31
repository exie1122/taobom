# TaoBOM

TaoBOM turns a KiCad bill of materials into a consolidated Taobao purchasing
plan. It searches for parts, compares candidate listings, limits the number of
shops, and produces a per-shop checklist.

Purchases and checkout remain manual.

## Desktop application

Start the launcher:

```powershell
python -m taobom gui
```

Choose a folder containing a `.kicad_sch` file or continue a saved session.
TaoBOM uses the installed KiCad CLI to export and validate the BOM, then stores
the session under `~/.taobom/sessions`.

The results window provides:

- selected listings grouped by shop colour
- estimated parts total based on seller lot sizes
- component details and match confidence
- QR codes for opening listings in the Taobao mobile app
- cell, column, link, and QR-sheet export
- manual SKU, price, and lot-size verification
- local cart-state tracking

Double-click a table cell to copy it. Right-click for cell and column copy
actions. Selecting a row opens its listing details and QR code on the right.

## Command line

```powershell
python -m taobom parts bom.csv --overrides spec.json
python -m taobom login
python -m taobom scrape bom.csv --overrides spec.json -o listings.json
python -m taobom verify listings.json -o listings.verified.json
python -m taobom plan bom.csv listings.verified.json --overrides spec.json -o plan.md
```

`login` opens Chrome for the initial Taobao sign-in. `scrape` reuses that
profile, captures Taobao search responses, and saves diagnostics under `raw/`.
If Taobao requests a CAPTCHA, complete it in the browser and continue from the
terminal.

Search-page prices can refer to the cheapest variant rather than the required
component. Run `verify` or use **Verify / edit** in the desktop application to
record the exact variant, price, and lot size.

## BOM formats

KiCad CSV exports require these fields:

```text
Reference,Value,Footprint,MPN
```

JLCPCB BOM exports using `Comment,Designator,Footprint,LCSC Part #` are also
accepted. LCSC catalogue numbers are not treated as manufacturer part numbers.

TaoBOM consolidates duplicate rows, expands reference ranges, excludes
non-purchasable footprints, extracts package names, and assigns stable part
identifiers.

## Listing format

```json
{
  "shops": [
    {
      "id": "shop-id",
      "name": "Shop name",
      "url": "https://shop.example",
      "ship_from": "Shenzhen",
      "rating": 4.8,
      "free_ship_over": 20,
      "ship_fee": 6
    }
  ],
  "listings": [
    {
      "part_id": "10k_0603",
      "shop_id": "shop-id",
      "title": "Listing title",
      "url": "https://item.taobao.com/item.htm?id=123",
      "price": 2.5,
      "lot_size": 100,
      "sku_note": "0603 10k 1%",
      "confidence": 0.9,
      "verified": false
    }
  ]
}
```

The solver selects one listing for each available part while accounting for
price, shipping thresholds, match confidence, and the configured shop limit.
PuLP/CBC is used when installed, with a greedy fallback otherwise.

## Tests

```powershell
python -m unittest discover -s tests -v
```

`core1106_stereo_bom.csv` and `examples/mock_listings.json` provide an offline
example data set.
