# taobom

KiCad BOM → consolidated Taobao purchase plan. Point it at a KiCad project's BOM,
give it candidate listings (mock for now, scraped later), and it solves which
shops to buy from so the fewest orders cover the whole board — then emits a
WeChat-friendly per-shop shopping checklist in Chinese.

No auto-buying. The output is a checklist a human executes in the Taobao app.

## Pipeline

```
kicad-cli sch export bom  →  bom.csv
python -m taobom parts  bom.csv --overrides spec.json   # inspect parsed parts + search terms
python -m taobom login                                  # one-time Taobao QR login (session persists)
python -m taobom scrape bom.csv --overrides spec.json -o listings.json
python -m taobom plan   bom.csv listings.json --overrides spec.json -o plan.md
```

`mock` generates fake listings instead of `scrape` for offline testing.
Scraping runs unattended after the one-time login — no LLM, no tokens:
Playwright drives your installed Chrome (separate profile in
`~/.taobom/chrome_profile`), intercepts Taobao's mtop JSON search API,
and paces requests human-slow. Raw payloads land in `raw/` so extraction
can be debugged offline. If a captcha appears, solve it in the window and
press Enter in the terminal.

Export the BOM from KiCad with at least `Reference,Value,Footprint,MPN` fields:

```
kicad-cli sch export bom --fields "Reference,Value,Footprint,MPN" --group-by Value,Footprint board.kicad_sch
```

## How it works

- **bom.py** — parses the CSV, expands ref ranges (`C2-C7`), drops test points /
  mounting holes, extracts the package (0603, SOT-23, …) from the footprint, and
  builds Chinese search queries per part.
- **solver.py** — one listing per part, minimizing part cost + shipping +
  `--shop-penalty` (CNY-equivalent hassle) per shop, hard-capped at `--max-shops`.
  ILP via pulp/CBC; greedy set-cover fallback if pulp is missing.
- **output.py** — per-shop markdown checklist: listing link, exact SKU (规格)
  to select, lots to buy, subtotal, shipping; flags low-confidence matches and
  parts that need manual sourcing.
- **mock.py** — deterministic fake shops/listings so the pipeline is testable
  end-to-end today.

## listings.json schema

The contract the future scraper must emit:

```json
{
  "shops":    [{"id", "name", "url", "ship_from", "rating", "free_ship_over", "ship_fee"}],
  "listings": [{"part_id", "shop_id", "title", "url", "price", "lot_size", "sku_note", "confidence"}]
}
```

`part_id` ties a listing to a Part (`taobom parts` shows the ids). `confidence`
below 0.8 gets a ⚠ human-review flag in the plan.

## Roadmap

1. ~~BOM parser + solver + plan output~~ (works, tested on scfyr2 core1106_stereo)
2. ~~Overrides layer (exclude/qty/kits/add) for real purchase specs~~
3. ~~Taobao search scraper (Playwright, persistent login, mtop interception)~~ — built, needs first real run
4. Item-page pass: lot sizes, SKU-variant matching (the 规格 dropdown), ship fees per shop
5. LCSC baseline tier: fill from LCSC first, Taobao for the leftovers

## Test data

`core1106_stereo_bom.csv` is the scfyr2 stereo camera board (Core1106 SoM +
2× SC035HGS camera modules + passives). `examples/mock_listings.json` covers it.
