# taobom

KiCad BOM → consolidated Taobao purchase plan.

Point it at a KiCad project's BOM, give it candidate listings, and it solves
which shops to buy from so the fewest orders cover the whole board — then emits
a per-shop shopping checklist in Chinese (Markdown, a phone-friendly HTML page,
plain text for WeChat, or JSON).

No auto-buying. The output is a checklist a human executes in the Taobao app.

## Install

Requirements:

- Python 3.10+
- [KiCad](https://www.kicad.org/) 7 or newer, only to export the BOM (`kicad-cli`)
- Google Chrome, used by the optional scraper

```powershell
git clone https://github.com/exie1122/taobom.git
cd taobom
python -m venv .venv
.venv\Scripts\activate
pip install -e .
taobom --version
```

On macOS/Linux use `python3 -m venv .venv && source .venv/bin/activate`.

`pipx install git+https://github.com/exie1122/taobom` works too. If you would
rather not install anything, every command below also runs as
`python -m taobom ...` from the repository root.

## 60-second demo (no Taobao account needed)

```powershell
taobom mock core1106_stereo_bom.csv
taobom plan core1106_stereo_bom.csv listings.json -o plan.html
```

Open `plan.html` on your phone. The mock data is deterministic, so the pipeline
is testable offline end to end.

## Real run

```powershell
# 1. Export the BOM (at least Reference, Value, Footprint, MPN)
kicad-cli sch export bom --fields "Reference,Value,Footprint,MPN" `
  --group-by Value,Footprint board.kicad_sch --output bom.csv

# 2. See what taobom thinks is purchasable, and the search terms it will use
taobom parts bom.csv --overrides spec.json

# 3. One-time Taobao login (QR scan; session persists in ~/.taobom)
taobom login

# 4. Search Taobao and save candidates (resumable, saves after every part)
taobom scrape bom.csv --overrides spec.json -o listings.json

# 5. Solve shop consolidation and write the checklist
taobom plan bom.csv listings.json --overrides spec.json -o plan.html
```

Scraping runs unattended after the one-time login — no LLM, no tokens:
Playwright drives your installed Chrome (separate profile in
`~/.taobom/chrome_profile`), intercepts Taobao's mtop JSON search API, and paces
requests human-slow. Raw payloads land in `raw/` so extraction can be debugged
offline. If a captcha appears, solve it in the window and press Enter.

Useful scraper options:

| Option | Why |
|---|---|
| `--reuse-raw` | Re-parse `raw/*.json` without touching Taobao (offline fix-ups, resume) |
| `--only PART_ID ...` | Re-scrape selected parts, keeping the rest of `listings.json` |
| `--boards 5` | Plan five builds without editing the overrides file |
| `--min-confidence 0.4` | Drop weaker title matches earlier |
| `--delay 10` | Be even gentler with the site |

A long run prints progress and an ETA and writes `listings.json` after every
part, so a crash or a captcha timeout never loses the whole search.

## Output formats

`taobom plan` picks the format from the output suffix (`.md`, `.html`, `.txt`,
`.json`) or accepts `--format`.

- **Markdown** — the default checklist, with low-confidence warnings and a
  fallback listing for every part.
- **HTML** — one self-contained mobile page. Link opens the Taobao app via
  `taobao://`, checkboxes remember what you already bought (localStorage), and
  it works offline.
- **Text** — no Markdown syntax, so it pastes cleanly into a WeChat chat.
- **JSON** — totals, items, unmatched parts and alternatives for scripts.

The plan also reports the cheapest single-shop alternative (when one shop can
supply everything) and how much the multi-shop plan saves, so the extra order
has a number attached to it.

## Options that change the answer

| Option | Default | Meaning |
|---|---|---|
| `--max-shops` | `5` | Preferred shop limit. If it cannot be met, the solver says how many shops are actually needed and returns that plan anyway. `0` = no limit. |
| `--strict-shops` | off | Fail instead of relaxing the limit. |
| `--shop-penalty` | `8` | CNY-equivalent hassle per extra shop. |
| `--risk-weight` | `15` | CNY penalty at 0% match confidence. |
| `--boards` | `1` | Multiply BOM quantities. |
| `--overrides` | – | Purchase-spec JSON layered over the BOM. |

## Overrides file

```json
{
  "exclude": ["PWR_IN_5V"],
  "qty": { "CORE1106G3": 2 },
  "search": { "CORE1106G3": ["幸狐 Core1106 核心板"] },
  "kits": [
    {
      "id": "cap_kit_0603",
      "value": "0603电容样品本",
      "qty": 1,
      "covers": ["0.1uF_0603", "1uF_0603"],
      "note": "每板需: 0.1uF x6, 1uF x3",
      "search_terms": ["0603 贴片电容 样品本 全套"]
    }
  ],
  "add": [
    {
      "id": "FH34SRJ_24S_0.5SH",
      "value": "FH34SRJ-24S-0.5SH",
      "mpn": "FH34SRJ-24S-0.5SH",
      "package": "FPC 24P 0.5mm",
      "qty": 10,
      "search_terms": ["FH34SRJ-24S-0.5SH"]
    }
  ]
}
```

`exclude` drops parts, `qty` overrides quantities, `search` replaces the
auto-generated Taobao queries, `kits` replaces several passives with one
assortment pack, and `add` injects parts that are not in the BOM. Unknown part
ids produce a warning; malformed sections and duplicate ids are errors.
`examples/scfyr2_overrides.json` is a worked example.

## BOM formats

KiCad CSV exports require `Reference,Value,Footprint`; `MPN` and `LCSC` are used
when present. Column names are matched case-insensitively and accept the common
variants:

- `Reference` / `Refs` / `Designator`
- `Value` / `Comment`
- `Footprint` / `Package`
- `MPN`, `LCSC`, `LCSC Part #`, `JLCPCB Part #`
- `DNP` / `Exclude from BOM`

JLCPCB exports using `Comment,Designator,Footprint,LCSC Part #` therefore work
without conversion, and the LCSC number is kept as a search term and shown in
the plan. If KiCad groups its export (`--group-by Value,Footprint`), one row is
one part; if it does not, taobom merges rows with the same value, package, MPN
and LCSC number and sums their quantities. Reference ranges (`C2-C7`) are
expanded. Test points, mounting holes, fiducials, logos, net ties and DNP rows
are skipped.

If no purchasable parts are found, the command now says so and prints the export
command to run, instead of exiting silently.

## listings.json schema

The contract the scraper emits and `plan` consumes:

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
      "sales": 1200,
      "stock": 500,
      "shop_url": "https://shop123.taobao.com",
      "source_query": "贴片电阻 0603 10k",
      "verified": false
    }
  ]
}
```

`part_id` ties a listing to a Part (`taobom parts` shows the ids). `confidence`
below 0.8 gets a ⚠ human-review flag in the plan. `sales`, `stock`, `shop_url`,
`source_query` and `verified` are optional. The file is validated on load:
unknown fields, unknown shop ids, negative prices, `lot_size <= 0` and
out-of-range confidence produce an actionable error instead of a traceback.

## How the plan is chosen

- **bom.py** — parses the CSV (KiCad or JLCPCB), merges duplicate rows, expands
  reference ranges, drops non-purchasable footprints and DNP lines, extracts the
  package (0603, SOT-23, …) from the footprint, and builds Chinese search
  queries per part.
- **solver.py** — one listing per part, minimizing part cost + shipping +
  `--shop-penalty` (CNY-equivalent hassle) per shop. ILP via pulp/CBC; greedy
  set-cover fallback if pulp is missing. The greedy fallback reports parts it
  cannot cover rather than dropping them.
- **output.py** — per-shop checklist with the listing link, exact SKU (规格) to
  select, lots to buy, subtotal and shipping; flags low-confidence matches and
  parts that need manual sourcing; lists backups per part and compares against
  the best single-shop plan.
- **scrape.py** — Playwright + mtop interception, title scoring, placeholder
  price filtering, pack-size inference from explicit "10只装"-style titles.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `error: BOM is missing a reference/designator column` | Re-export with `--fields "Reference,Value,Footprint,MPN"`. |
| `error: No purchasable parts found` | The file may contain only test points, or the BOM fields are empty. Run `taobom parts` to inspect. |
| `Not logged in` | Run `taobom login`; the Chrome profile is in `~/.taobom/chrome_profile`. |
| CAPTCHA loop | Raise `--delay`, stop the run, solve the captcha manually, then `--reuse-raw`. |
| `error: covering every part needs at least N shops` | Raise `--max-shops` (or set it to `0`); drop `--strict-shops` to let the solver relax it. |
| Totals look too low/high | Search pages show a starting price, not the 规格 price. Check `lot_size` and the SKU in the plan; the HTML page links straight to the item. |
| Chinese text is mojibake in the console | Use Windows Terminal / PowerShell 7; taobom reconfigures stdout to UTF-8 but legacy consoles can still struggle. |

## Development

```powershell
pip install -e .
python -m unittest discover -s tests -v
```

Layout: `taobom/bom.py`, `solver.py`, `output.py`, `scrape.py`, `mock.py`,
`cli.py`, `models.py`; fixtures in `examples/`; tests in `tests/`. CI runs the
suite on Python 3.11–3.13.

## Limitations

- Prices come from search results and are per lot as inferred from the title.
  The exact 规格 (variant) price still needs the item page or `listings.json`
  edits; the plan flags that.
- Scraping Taobao is against nobody's interest here but it is still scraping;
  keep `--delay` high and solve captchas yourself.
- No cart or checkout automation, by design.

## Roadmap

1. ~~BOM parser + solver + plan output~~
2. ~~Overrides layer (exclude/qty/kits/add)~~
3. ~~Taobao search scraper (Playwright, persistent login, mtop interception)~~
4. Item-page pass: exact lot sizes, SKU-variant matching, per-shop ship fees
5. LCSC baseline tier: fill from LCSC first, Taobao for the leftovers

## Test data

`core1106_stereo_bom.csv` is the scfyr2 stereo camera board (Core1106 SoM +
2× SC035HGS camera modules + passives). `examples/mock_listings.json` covers it.

## License

MIT — see `LICENSE`.
