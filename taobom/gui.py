"""TaoBOM desktop interface."""
from __future__ import annotations

import dataclasses
import colorsys
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from .bom import apply_overrides, load_bom
from .cli import load_listings, save_listings
from .models import Listing
from .output import render_plan
from .solver import solve

SHOP_COLORS = (
    "#E3F2FD",
    "#E8F5E9",
    "#FFF3E0",
    "#F3E5F5",
    "#FFFDE7",
    "#E0F7FA",
    "#FCE4EC",
    "#ECEFF1",
)


def shop_color(index: int) -> str:
    if index < len(SHOP_COLORS):
        return SHOP_COLORS[index]
    hue = (index * 0.618033988749895) % 1.0
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.16, 1.0)
    return f"#{round(red * 255):02X}{round(green * 255):02X}{round(blue * 255):02X}"


def apply_window_icon(window) -> None:
    icon_dir = Path(__file__).with_name("assets")
    ico_path = icon_dir / "taobom-icon.ico"
    png_path = icon_dir / "taobom-icon.png"
    try:
        if os.name == "nt" and ico_path.exists():
            window.iconbitmap(default=str(ico_path))
        if png_path.exists():
            photo = window.tk.call(
                "image", "create", "photo", "-file", str(png_path)
            )
            window.tk.call("wm", "iconphoto", window._w, True, photo)
            window._taobom_icon_photo = photo
    except Exception:
        pass


def configure_windows_app_identity() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "TaoBOM.Desktop"
        )
    except Exception:
        pass


THEME = {
    "orange": "#DF6A3A",
    "orange_hover": "#CE5B2D",
    "orange_pale": "#FBEDE6",
    "orange_border": "#DEAA91",
    "background": "#F5F5F5",
    "card": "#FFFFFF",
    "text": "#222222",
    "muted": "#6B6B6B",
    "border": "#DCDCDC",
}


def apply_taobao_theme(window):
    from tkinter import ttk

    colors = THEME
    window.configure(background=colors["background"])
    style = ttk.Style(window)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    style.configure(
        ".", background=colors["background"], foreground=colors["text"],
        font=("Segoe UI", 9),
    )
    style.configure("TFrame", background=colors["background"])
    style.configure("Card.TFrame", background=colors["card"])
    style.configure("Toolbar.TFrame", background=colors["orange_pale"])
    style.configure("TLabel", background=colors["background"], foreground=colors["text"])
    style.configure("Card.TLabel", background=colors["card"])
    style.configure(
        "Muted.TLabel", background=colors["background"], foreground=colors["muted"],
    )
    style.configure(
        "Brand.TFrame", background=colors["orange"], padding=(24, 18),
    )
    style.configure(
        "BrandTitle.TLabel", background=colors["orange"], foreground="#FFFFFF",
        font=("Segoe UI", 25, "bold"),
    )
    style.configure(
        "BrandSubtitle.TLabel", background=colors["orange"], foreground="#FFF3EC",
        font=("Segoe UI", 10),
    )
    style.configure(
        "Summary.TLabel", background=colors["orange_pale"],
        foreground="#A83200", font=("Segoe UI", 11, "bold"),
    )
    style.configure(
        "Status.TLabel", background="#EEEEEE", foreground=colors["muted"],
    )
    style.configure(
        "TButton", background=colors["card"], foreground=colors["text"],
        bordercolor=colors["border"], lightcolor=colors["card"],
        darkcolor=colors["border"], padding=(10, 6),
    )
    style.map(
        "TButton",
        background=[("active", colors["orange_pale"]), ("pressed", "#FFE2D1")],
        bordercolor=[("active", colors["orange_border"])],
    )
    style.configure(
        "Accent.TButton", background=colors["orange"], foreground="#FFFFFF",
        bordercolor=colors["orange"], lightcolor=colors["orange"],
        darkcolor=colors["orange"], font=("Segoe UI", 9, "bold"),
    )
    style.map(
        "Accent.TButton",
        background=[
            ("disabled", "#DFA58B"), ("pressed", "#B94E24"),
            ("active", colors["orange_hover"]),
        ],
        foreground=[("disabled", "#FFF3EC"), ("active", "#FFFFFF")],
        bordercolor=[("active", colors["orange_hover"])],
    )
    style.configure(
        "Treeview", background=colors["card"], fieldbackground=colors["card"],
        foreground=colors["text"], bordercolor=colors["border"], rowheight=28,
    )
    style.map(
        "Treeview",
        background=[("selected", colors["orange"])],
        foreground=[("selected", "#FFFFFF")],
    )
    style.configure(
        "Treeview.Heading", background=colors["orange_pale"],
        foreground="#8E2D00", bordercolor=colors["orange_border"],
        font=("Segoe UI", 9, "bold"), padding=(6, 6),
    )
    style.map("Treeview.Heading", background=[("active", "#FFE2D1")])
    style.configure(
        "TLabelframe", background=colors["background"],
        bordercolor=colors["border"], relief="solid",
    )
    style.configure(
        "TLabelframe.Label", background=colors["background"],
        foreground="#8E2D00", font=("Segoe UI", 9, "bold"),
    )
    style.configure("TNotebook", background=colors["background"], borderwidth=0)
    style.configure(
        "TNotebook.Tab", background="#EAEAEA", foreground=colors["muted"],
        padding=(12, 7),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", colors["orange_pale"])],
        foreground=[("selected", "#8E2D00")],
    )
    return style


@dataclass(frozen=True)
class GuiSession:
    session_file: Path
    root: Path
    bom: Path
    overrides: Path | None
    listings: Path
    plan: Path
    cart_state: Path
    max_shops: int = 6


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (root / path).resolve()


def load_session(path: str | Path) -> GuiSession:
    session_path = Path(path).resolve()
    data = json.loads(session_path.read_text(encoding="utf-8"))
    root = session_path.parent
    required = {"bom", "listings", "plan"}
    missing = required - set(data)
    if missing:
        raise ValueError(f"GUI session is missing: {', '.join(sorted(missing))}")
    overrides = data.get("overrides")
    return GuiSession(
        session_file=session_path,
        root=root,
        bom=_resolve(root, data["bom"]),
        overrides=_resolve(root, overrides) if overrides else None,
        listings=_resolve(root, data["listings"]),
        plan=_resolve(root, data["plan"]),
        cart_state=_resolve(root, data.get("cart_state", "cart-state.json")),
        max_shops=int(data.get("max_shops", 6)),
    )


def find_kicad_cli() -> Path | None:
    found = shutil.which("kicad-cli")
    if found:
        return Path(found)
    candidates: list[Path] = []
    for base in (Path("C:/Program Files/KiCad"), Path("C:/Program Files (x86)/KiCad")):
        if base.exists():
            candidates.extend(base.glob("*/bin/kicad-cli.exe"))
    return sorted(candidates, reverse=True)[0] if candidates else None


def discover_kicad_schematics(folder: str | Path) -> list[Path]:
    root = Path(folder).resolve()
    ignored = {".git", ".taobom", "backups", "archive", "build"}
    candidates = [
        path for path in root.rglob("*.kicad_sch")
        if not any(part.casefold() in ignored for part in path.relative_to(root).parts)
        and not path.name.casefold().endswith(".bak.kicad_sch")
    ]

    def score(path: Path):
        matching_project = path.with_suffix(".kicad_pro").exists()
        at_root = path.parent == root
        return (not matching_project, not at_root, -path.stat().st_mtime, path.name.casefold())

    return sorted(candidates, key=score)


def _session_registry_path() -> Path:
    return Path.home() / ".taobom" / "gui-recent.json"


def load_recent_sessions(registry: Path | None = None) -> list[Path]:
    path = registry or _session_registry_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [
        Path(value) for value in data.get("sessions", [])
        if isinstance(value, str) and Path(value).exists()
    ]


def remember_session(session_file: Path, registry: Path | None = None) -> None:
    path = registry or _session_registry_path()
    existing = [item for item in load_recent_sessions(path) if item != session_file]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"sessions": [str(session_file), *map(str, existing[:7])]},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )


def create_session_from_kicad(
    project_folder: str | Path,
    schematic: str | Path,
    session_base: Path | None = None,
    kicad_cli: Path | None = None,
    remember: bool = True,
) -> Path:
    project = Path(project_folder).resolve()
    schematic_path = Path(schematic).resolve()
    cli = kicad_cli or find_kicad_cli()
    if cli is None:
        raise RuntimeError("KiCad CLI was not found. Install KiCad 8 or newer.")
    digest = hashlib.sha1(str(project).casefold().encode("utf-8")).hexdigest()[:8]
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", project.name).strip("-") or "project"
    root = (session_base or Path.home() / ".taobom" / "sessions") / f"{safe_name}-{digest}"
    root.mkdir(parents=True, exist_ok=True)
    bom = root / "bom.csv"
    command = [
        str(cli), "sch", "export", "bom",
        "--output", str(bom),
        "--fields", "Reference,Value,Footprint,MPN",
        "--labels", "Reference,Value,Footprint,MPN",
        "--group-by", "Value,Footprint,MPN",
        "--exclude-dnp",
        str(schematic_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "KiCad export failed")
    parts = load_bom(bom)
    listings = root / "listings.json"
    if not listings.exists():
        listings.write_text('{"shops": [], "listings": []}\n', encoding="utf-8")
    plan = root / "shopping-list.txt"
    if not plan.exists():
        preview = [
            "TaoBOM project imported successfully.",
            "",
            f"Schematic: {schematic_path}",
            f"BOM line items: {len(parts)}",
            "",
            "Use “Taobao login” once, then “Search Taobao” to find listings.",
            "",
            "Imported parts:",
        ]
        preview.extend(f"- {part.qty} × {part.display}" for part in parts)
        plan.write_text("\n".join(preview), encoding="utf-8")
    session_file = root / "session.json"
    session_file.write_text(
        json.dumps({
            "name": project.name,
            "project_folder": str(project),
            "schematic": str(schematic_path),
            "bom": "bom.csv",
            "listings": "listings.json",
            "plan": "shopping-list.txt",
            "cart_state": "cart-state.json",
            "max_shops": 6,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if remember:
        remember_session(session_file)
    return session_file


def _state_key(listing: Listing) -> str:
    return f"{listing.part_id}|{listing.url or listing.title}"


def _links_text(listings: list[Listing]) -> str:
    return "\n".join(listing.url for listing in listings if listing.url)


def _estimated_parts_total(listings: list[Listing], parts) -> float:
    return sum(
        listing.cost_for(parts[listing.part_id].qty)
        for listing in listings
        if listing.part_id in parts
    )


def _item_id(url: str) -> str:
    match = re.search(r"[?&]id=(\d+)", url)
    return match.group(1) if match else ""


def make_qr_image(value: str, box_size: int = 8):
    """Create a plain high-contrast QR image suitable for phone scanning."""
    import qrcode

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=4,
    )
    qr.add_data(value)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").get_image()


def export_qr_sheet(root: Path, listings: list[Listing], shops, parts) -> Path:
    qr_dir = root / "qr"
    qr_dir.mkdir(parents=True, exist_ok=True)
    cards = []
    for number, listing in enumerate(listings, start=1):
        safe_id = re.sub(r"[^A-Za-z0-9]+", "-", listing.part_id).strip("-")
        filename = f"{number:02d}-{safe_id}.png"
        make_qr_image(listing.url, box_size=7).save(qr_dir / filename)
        shop = shops.get(listing.shop_id)
        part = parts.get(listing.part_id)
        cards.append(
            "<article>"
            f"<div class='group'>#{number}</div>"
            f"<h2>{html.escape(part.display if part else listing.part_id)}</h2>"
            f"<p>{html.escape(shop.name if shop else listing.shop_id)}</p>"
            f"<img src='qr/{html.escape(filename)}' alt='QR code'>"
            f"<p class='id'>Item ID: {html.escape(_item_id(listing.url) or 'unknown')}</p>"
            f"<p class='url'>{html.escape(listing.url)}</p>"
            "</article>"
        )
    sheet = root / "qr-sheet.html"
    sheet.write_text(
        """<!doctype html>
<html lang="en"><meta charset="utf-8">
<title>Taobom QR sheet</title>
<style>
body{font:14px system-ui;margin:24px;background:#f5f5f5}
h1{margin:0 0 8px}.note{margin:0 0 22px;color:#444}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}
article{position:relative;background:white;border:1px solid #ccc;border-radius:10px;
padding:16px;text-align:center;break-inside:avoid}
.group{position:absolute;top:10px;left:10px;font-weight:700;background:#e3f2fd;
padding:4px 8px;border-radius:5px}
h2{font-size:16px;margin:4px 28px 5px}p{margin:4px}
img{width:230px;height:230px;image-rendering:pixelated}
.id{font-weight:700}.url{font-size:9px;color:#666;overflow-wrap:anywhere}
@media print{body{background:white;margin:8mm}article{page-break-inside:avoid}}
</style>
<h1>Taobom — scan with the Taobao app</h1>
<p class="note">Open Taobao's scanner and scan one code at a time. Confirm the
item and exact variant before adding it to the cart.</p>
<main>"""
        + "\n".join(cards)
        + "</main></html>",
        encoding="utf-8",
    )
    return sheet


class FirstBootLauncher:
    def __init__(self, default_session: Path | None = None):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.selected_session: Path | None = None
        self.window = tk.Tk()
        apply_window_icon(self.window)
        self.window.title("TaoBOM")
        self.window.geometry("720x520")
        self.window.minsize(620, 430)
        apply_taobao_theme(self.window)

        header = ttk.Frame(self.window, style="Brand.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text="TaoBOM", style="BrandTitle.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Turn a KiCad project into a consolidated Taobao parts plan.",
            style="BrandSubtitle.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        actions = ttk.Frame(self.window, padding=(24, 4, 24, 18))
        actions.pack(fill="x")
        ttk.Button(
            actions, text="Choose KiCad project folder…",
            command=self.choose_project, width=32, style="Accent.TButton",
        ).pack(side="left")
        ttk.Button(
            actions, text="Open session file…",
            command=self.choose_session_file, width=24,
        ).pack(side="left", padx=(10, 0))

        recent_box = ttk.LabelFrame(
            self.window, text="Continue a previous session", padding=12
        )
        recent_box.pack(fill="both", expand=True, padx=24, pady=(0, 14))
        recent = load_recent_sessions()
        if default_session and default_session.exists() and default_session not in recent:
            recent.append(default_session)
        if recent:
            for session_file in recent:
                try:
                    data = json.loads(session_file.read_text(encoding="utf-8"))
                    name = data.get("name") or session_file.parent.name
                    detail = data.get("project_folder") or str(session_file.parent)
                except (OSError, ValueError):
                    name, detail = session_file.parent.name, str(session_file)
                row = ttk.Frame(recent_box, padding=(4, 5))
                row.pack(fill="x")
                text = ttk.Frame(row)
                text.pack(side="left", fill="x", expand=True)
                ttk.Label(text, text=name, font=("Segoe UI", 10, "bold")).pack(anchor="w")
                ttk.Label(text, text=detail, style="Muted.TLabel").pack(anchor="w")
                ttk.Button(
                    row, text="Continue",
                    command=lambda value=session_file: self.open_session(value),
                ).pack(side="right")
        else:
            ttk.Label(
                recent_box,
                text="No previous sessions yet. Choose a KiCad project folder to begin.",
                style="Muted.TLabel",
            ).pack(anchor="center", pady=35)

        self.status = self.tk.StringVar(value="")
        ttk.Label(
            self.window, textvariable=self.status, style="Muted.TLabel",
            padding=(24, 0, 24, 14),
        ).pack(fill="x")

    def choose_schematic(self, paths: list[Path]) -> Path | None:
        if len(paths) == 1:
            return paths[0]
        result: list[Path] = []
        dialog = self.tk.Toplevel(self.window)
        dialog.title("Choose the main KiCad schematic")
        dialog.geometry("620x360")
        dialog.transient(self.window)
        dialog.grab_set()
        self.ttk.Label(
            dialog,
            text="Multiple schematics were found. Select the main project schematic:",
            padding=12,
        ).pack(fill="x")
        listbox = self.tk.Listbox(dialog, font=("Consolas", 9))
        listbox.pack(fill="both", expand=True, padx=12)
        for path in paths:
            listbox.insert("end", str(path))
        listbox.selection_set(0)

        def accept():
            selection = listbox.curselection()
            if selection:
                result.append(paths[selection[0]])
                dialog.destroy()

        buttons = self.ttk.Frame(dialog, padding=12)
        buttons.pack(fill="x")
        self.ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
        self.ttk.Button(buttons, text="Use schematic", command=accept).pack(
            side="right", padx=(0, 8)
        )
        listbox.bind("<Double-1>", lambda _event: accept())
        self.window.wait_window(dialog)
        return result[0] if result else None

    def choose_project(self):
        from tkinter import filedialog, messagebox

        folder = filedialog.askdirectory(
            title="Choose a folder containing a KiCad project",
            parent=self.window,
        )
        if not folder:
            return
        paths = discover_kicad_schematics(folder)
        if not paths:
            messagebox.showerror(
                "No KiCad schematic found",
                "This folder does not contain a .kicad_sch file.",
                parent=self.window,
            )
            return
        schematic = self.choose_schematic(paths)
        if schematic is None:
            return
        self.status.set("Exporting the KiCad BOM…")
        self.window.update_idletasks()
        try:
            session = create_session_from_kicad(folder, schematic)
        except Exception as exc:
            messagebox.showerror("Could not import project", str(exc), parent=self.window)
            self.status.set("")
            return
        self.open_session(session)

    def choose_session_file(self):
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Open a TaoBOM session",
            filetypes=(("TaoBOM session", "session.json"), ("JSON files", "*.json")),
            parent=self.window,
        )
        if path:
            self.open_session(Path(path))

    def open_session(self, path: Path):
        try:
            load_session(path)
        except Exception as exc:
            from tkinter import messagebox
            messagebox.showerror("Invalid TaoBOM session", str(exc), parent=self.window)
            return
        remember_session(path.resolve())
        self.selected_session = path.resolve()
        self.window.destroy()

    def run(self) -> Path | None:
        self.window.mainloop()
        return self.selected_session


class TaobomGui:
    def __init__(self, session: GuiSession):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.session = session
        self.shops = {}
        self.listings: list[Listing] = []
        self.parts = {}
        self.cart_state: dict[str, bool] = {}
        self.active_cell: tuple[str, str] | None = None
        self.shop_colors_by_tag: dict[str, str] = {}

        self.window = tk.Tk()
        apply_window_icon(self.window)
        self.window.title(f"TaoBOM — {session.root.name}")
        self.window.geometry("1280x780")
        self.window.minsize(900, 560)

        apply_taobao_theme(self.window)

        toolbar = ttk.Frame(self.window, padding=8, style="Toolbar.TFrame")
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Reload", command=self.reload).pack(side="left")
        ttk.Button(toolbar, text="Taobao login", command=self.start_login).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(
            toolbar, text="Search Taobao", command=self.start_search,
            style="Accent.TButton",
        ).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(toolbar, text="Copy all links", command=self.copy_all_links).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(toolbar, text="Export QR sheet", command=self.export_qr_sheet).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(toolbar, text="Regenerate plan", command=self.regenerate_plan).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(toolbar, text="Open results folder", command=self.open_folder).pack(
            side="right"
        )

        self.summary = ttk.Label(
            self.window, text="", style="Summary.TLabel", padding=(10, 2, 10, 8)
        )
        self.summary.pack(fill="x")

        self.legend = ttk.Frame(self.window, padding=(10, 0, 10, 8))
        self.legend.pack(fill="x")

        workspace = ttk.Panedwindow(self.window, orient="horizontal")
        workspace.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        pane = ttk.Panedwindow(workspace, orient="vertical")
        workspace.add(pane, weight=4)

        table_frame = ttk.Frame(pane)
        columns = (
            "group", "cart", "verified", "part", "qty", "shop", "price",
            "lot", "confidence", "title",
        )
        self.tree = ttk.Treeview(
            table_frame, columns=columns, show="headings", selectmode="browse"
        )
        labels = {
            "group": "Group", "cart": "Cart", "verified": "Verified", "part": "Part",
            "qty": "Need", "shop": "Shop", "price": "¥ / lot",
            "lot": "Lot size", "confidence": "Match", "title": "Listing",
        }
        widths = {
            "group": 55, "cart": 55, "verified": 70, "part": 210,
            "qty": 55, "shop": 160,
            "price": 75, "lot": 70, "confidence": 65, "title": 470,
        }
        for column in columns:
            self.tree.heading(column, text=labels[column])
            self.tree.column(column, width=widths[column], minwidth=45)
        scroll_y = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        scroll_x = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.tree.bind("<Button-1>", self.remember_cell, add="+")
        self.tree.bind("<Double-1>", self.copy_clicked_cell)
        self.tree.bind("<Control-c>", self.copy_active_cell)
        self.tree.bind("<Control-C>", self.copy_active_cell)
        self.tree.bind("<Button-3>", self.show_copy_menu)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.update_detail())
        self.copy_menu = tk.Menu(self.window, tearoff=False)
        self.copy_menu.add_command(label="Copy cell", command=self.copy_active_cell)
        self.copy_menu.add_command(label="Copy entire column", command=self.copy_active_column)
        self.copy_menu.add_separator()
        self.copy_menu.add_command(label="Copy all links", command=self.copy_all_links)
        pane.add(table_frame, weight=3)

        plan_frame = ttk.LabelFrame(pane, text="Shopping plan")
        self.plan_text = tk.Text(
            plan_frame, wrap="word", font=("Consolas", 10), padx=8, pady=8,
            background=THEME["card"], foreground=THEME["text"],
            selectbackground=THEME["orange"], selectforeground="#FFFFFF",
            relief="flat", borderwidth=0,
        )
        plan_scroll = ttk.Scrollbar(
            plan_frame, orient="vertical", command=self.plan_text.yview
        )
        self.plan_text.configure(yscrollcommand=plan_scroll.set)
        self.plan_text.pack(side="left", fill="both", expand=True)
        plan_scroll.pack(side="right", fill="y")
        pane.add(plan_frame, weight=2)

        detail_notebook = ttk.Notebook(workspace, width=330)
        detail = ttk.Frame(detail_notebook, padding=12)
        detail_notebook.add(detail, text="Selected component")
        workspace.add(detail_notebook, weight=1)

        self.detail_part = tk.StringVar(value="Select a component")
        self.detail_shop = tk.StringVar(value="")
        self.detail_group = tk.StringVar(value="")
        self.detail_numbers = tk.StringVar(value="")
        self.detail_status = tk.StringVar(value="")
        self.detail_item_id = tk.StringVar(value="")
        self.detail_title = tk.StringVar(value="")
        self.detail_cart_button = tk.StringVar(value="Mark as in cart")
        ttk.Label(
            detail, textvariable=self.detail_part, font=("Segoe UI", 12, "bold"),
            wraplength=300, justify="center",
        ).pack(fill="x", pady=(2, 3))
        ttk.Label(
            detail, textvariable=self.detail_shop, font=("Segoe UI", 10),
            wraplength=300, justify="center",
        ).pack(fill="x")
        self.detail_group_label = tk.Label(
            detail, textvariable=self.detail_group, relief="solid",
            borderwidth=1, padx=7, pady=3,
        )
        self.detail_group_label.pack(pady=(7, 4))
        self.detail_qr_label = ttk.Label(detail, padding=5)
        self.detail_qr_label.pack()
        ttk.Label(
            detail, textvariable=self.detail_item_id, font=("Consolas", 10, "bold"),
        ).pack()
        ttk.Separator(detail).pack(fill="x", pady=8)
        ttk.Label(
            detail, textvariable=self.detail_numbers, wraplength=300,
            justify="left",
        ).pack(fill="x")
        ttk.Label(
            detail, textvariable=self.detail_status, wraplength=300,
            justify="left",
        ).pack(fill="x", pady=(5, 0))
        ttk.Label(
            detail, textvariable=self.detail_title, wraplength=300,
            justify="left",
        ).pack(fill="x", pady=(8, 10))

        actions = ttk.Frame(detail)
        actions.pack(fill="x", side="bottom")
        ttk.Button(
            actions, text="Open in Taobao", command=self.open_selected,
            style="Accent.TButton",
        ).grid(
            row=0, column=0, sticky="ew", padx=(0, 4), pady=3
        )
        ttk.Button(actions, text="Copy link", command=self.copy_link).grid(
            row=0, column=1, sticky="ew", padx=(4, 0), pady=3
        )
        ttk.Button(actions, text="Verify / edit", command=self.verify_selected).grid(
            row=1, column=0, sticky="ew", padx=(0, 4), pady=3
        )
        ttk.Button(
            actions, textvariable=self.detail_cart_button, command=self.toggle_cart
        ).grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=3)
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)

        self.status = ttk.Label(
            self.window, anchor="w", padding=(8, 5), style="Status.TLabel"
        )
        self.status.pack(fill="x")
        self.reload()

    def selected_index(self) -> int | None:
        selected = self.tree.selection()
        return int(selected[0]) if selected else None

    def load_cart_state(self):
        if self.session.cart_state.exists():
            data = json.loads(self.session.cart_state.read_text(encoding="utf-8"))
            self.cart_state = {str(key): bool(value) for key, value in data.items()}
        else:
            self.cart_state = {}

    def save_cart_state(self):
        self.session.cart_state.write_text(
            json.dumps(self.cart_state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def reload(self):
        from tkinter import messagebox

        try:
            self.shops, self.listings = load_listings(self.session.listings)
            parts = load_bom(self.session.bom)
            if self.session.overrides:
                overrides = json.loads(self.session.overrides.read_text(encoding="utf-8"))
                parts = apply_overrides(parts, overrides)
            self.parts = {part.id: part for part in parts}
            self.load_cart_state()
        except Exception as exc:
            messagebox.showerror("Could not load results", str(exc), parent=self.window)
            return

        previous_selection = self.tree.selection()
        previous_id = previous_selection[0] if previous_selection else None
        self.tree.delete(*self.tree.get_children())
        ordered_shops = sorted(
            {listing.shop_id for listing in self.listings},
            key=lambda shop_id: self.shops[shop_id].name.casefold(),
        )
        shop_groups = {
            shop_id: (number + 1, shop_color(number))
            for number, shop_id in enumerate(ordered_shops)
        }
        for child in self.legend.winfo_children():
            child.destroy()
        self.shop_colors_by_tag.clear()
        self.ttk.Label(self.legend, text="Same shop:", padding=(0, 2)).pack(side="left")
        for shop_id in ordered_shops:
            group_number, color = shop_groups[shop_id]
            self.tk.Label(
                self.legend,
                text=f" S{group_number}  {self.shops[shop_id].name} ",
                background=color,
                relief="solid",
                borderwidth=1,
                padx=4,
                pady=2,
            ).pack(side="left", padx=(0, 6))
            tag = f"shop_{group_number}"
            self.shop_colors_by_tag[tag] = color
            self.tree.tag_configure(tag, background=color)

        display_order = sorted(
            enumerate(self.listings),
            key=lambda pair: (
                shop_groups[pair[1].shop_id][0],
                pair[1].part_id.casefold(),
            ),
        )
        for index, listing in display_order:
            part = self.parts.get(listing.part_id)
            qty = part.qty if part else "?"
            shop = self.shops.get(listing.shop_id)
            group_number, _color = shop_groups[listing.shop_id]
            self.tree.insert("", "end", iid=str(index), values=(
                f"S{group_number}",
                "Yes" if self.cart_state.get(_state_key(listing)) else "",
                "Yes" if listing.verified else "Review",
                part.display if part else listing.part_id,
                qty,
                shop.name if shop else listing.shop_id,
                f"{listing.price:.2f}",
                listing.lot_size,
                f"{listing.confidence:.0%}",
                listing.title,
            ), tags=(f"shop_{group_number}",))
        verified = sum(listing.verified for listing in self.listings)
        cart = sum(
            bool(self.cart_state.get(_state_key(listing))) for listing in self.listings
        )
        estimated_total = _estimated_parts_total(self.listings, self.parts)
        self.summary.configure(
            text=(
                f"{len(self.parts)} BOM parts  ·  "
                f"{len(self.listings)} selected listings  ·  "
                f"{len({listing.shop_id for listing in self.listings})} shops  ·  "
                f"estimated parts ¥{estimated_total:.2f} (shipping excluded)  ·  "
                f"{verified} verified  ·  {cart} marked in cart"
            )
        )
        text = (
            self.session.plan.read_text(encoding="utf-8")
            if self.session.plan.exists()
            else "No plan has been generated yet."
        )
        self.plan_text.configure(state="normal")
        self.plan_text.delete("1.0", "end")
        self.plan_text.insert("1.0", text)
        self.plan_text.configure(state="disabled")
        children = self.tree.get_children()
        selected_id = previous_id if previous_id in children else (children[0] if children else None)
        if selected_id is not None:
            self.tree.selection_set(selected_id)
            self.tree.focus(selected_id)
            self.tree.see(selected_id)
        self.update_detail()
        self.status.configure(text=str(self.session.root))

    def launch_cli_task(self, arguments: list[str], description: str):
        from tkinter import messagebox

        if getattr(self, "background_process", None) is not None:
            if self.background_process.poll() is None:
                messagebox.showinfo(
                    "Task already running",
                    "Wait for the current TaoBOM task to finish.",
                    parent=self.window,
                )
                return
        executable = Path(sys.executable)
        if executable.name.casefold() == "pythonw.exe":
            console_python = executable.with_name("python.exe")
            if console_python.exists():
                executable = console_python
        creationflags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
        self.background_process = subprocess.Popen(
            [str(executable), "-m", "taobom", *arguments],
            cwd=str(self.session.root),
            creationflags=creationflags,
        )
        self.status.configure(text=f"{description} started in a separate window…")
        self.window.after(1000, self.poll_cli_task)

    def poll_cli_task(self):
        process = getattr(self, "background_process", None)
        if process is None:
            return
        code = process.poll()
        if code is None:
            self.window.after(1000, self.poll_cli_task)
            return
        self.background_process = None
        if code == 0:
            self.reload()
            self.status.configure(text="Task completed successfully.")
        else:
            self.status.configure(
                text=f"Task exited with code {code}; review the console window."
            )

    def start_login(self):
        self.launch_cli_task(["login"], "Taobao login")

    def start_search(self):
        from tkinter import messagebox

        if self.listings and not messagebox.askyesno(
            "Replace current search results?",
            "Searching again will replace the currently selected listings for this "
            "session. Continue?",
            parent=self.window,
        ):
            return
        self.launch_cli_task(
            ["search-session", str(self.session.session_file)],
            "Taobao search",
        )

    def update_detail(self):
        from PIL import ImageTk

        index = self.selected_index()
        if index is None:
            self.detail_part.set("Select a component")
            self.detail_shop.set("")
            self.detail_group.set("")
            self.detail_numbers.set("")
            self.detail_status.set("")
            self.detail_item_id.set("")
            self.detail_title.set("")
            self.detail_qr_label.configure(image="")
            return
        listing = self.listings[index]
        part = self.parts.get(listing.part_id)
        shop = self.shops.get(listing.shop_id)
        row = str(index)
        group = self.tree.set(row, "group")
        tags = self.tree.item(row, "tags")
        color = self.shop_colors_by_tag.get(tags[0], "#ECEFF1") if tags else "#ECEFF1"
        self.detail_group_label.configure(background=color)
        self.detail_group.set(f"{group} · same color means same shop")
        self.detail_part.set(part.display if part else listing.part_id)
        self.detail_shop.set(shop.name if shop else listing.shop_id)
        if part:
            lots = listing.lots_for(part.qty)
            units = listing.units_for(part.qty)
            total = listing.cost_for(part.qty)
            self.detail_numbers.set(
                f"Need: {part.qty} units\n"
                f"Buy: {lots} lot{'s' if lots != 1 else ''} × {listing.lot_size} "
                f"= {units} units\n"
                f"Price: ¥{listing.price:.2f} / lot · estimated ¥{total:.2f}"
            )
        else:
            self.detail_numbers.set(
                f"Price: ¥{listing.price:.2f} / lot · lot size {listing.lot_size}"
            )
        self.detail_status.set(
            f"Match confidence: {listing.confidence:.0%}\n"
            f"Verification: {'confirmed' if listing.verified else 'not yet verified'}"
        )
        self.detail_item_id.set(f"Taobao item ID: {_item_id(listing.url) or 'unknown'}")
        self.detail_title.set(listing.title)
        self.detail_cart_button.set(
            "Remove cart mark"
            if self.cart_state.get(_state_key(listing))
            else "Mark as in cart"
        )
        photo = ImageTk.PhotoImage(make_qr_image(listing.url, box_size=5))
        self.detail_qr_photo = photo
        self.detail_qr_label.configure(image=photo)

    def remember_cell(self, event):
        row = self.tree.identify_row(event.y)
        display_column = self.tree.identify_column(event.x)
        if not row or not display_column:
            return
        column_index = int(display_column[1:]) - 1
        columns = self.tree["columns"]
        if 0 <= column_index < len(columns):
            self.active_cell = (row, columns[column_index])

    def _copy_text(self, value: str, status: str):
        self.window.clipboard_clear()
        self.window.clipboard_append(value)
        self.window.update_idletasks()
        self.status.configure(text=status)

    def copy_clicked_cell(self, event):
        self.remember_cell(event)
        self.copy_active_cell()
        return "break"

    def copy_active_cell(self, _event=None):
        if not self.active_cell:
            return "break"
        row, column = self.active_cell
        self._copy_text(str(self.tree.set(row, column)), "Cell copied.")
        return "break"

    def copy_active_column(self):
        if not self.active_cell:
            return
        _row, column = self.active_cell
        values = [str(self.tree.set(row, column)) for row in self.tree.get_children()]
        self._copy_text("\n".join(values), f"Column '{column}' copied.")

    def show_copy_menu(self, event):
        self.remember_cell(event)
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
            self.tree.focus(row)
        self.copy_menu.tk_popup(event.x_root, event.y_root)

    def open_selected(self):
        index = self.selected_index()
        if index is not None and self.listings[index].url:
            webbrowser.open(self.listings[index].url)
            self.status.configure(text="Opened listing in your browser.")

    def copy_link(self):
        index = self.selected_index()
        if index is None:
            return
        self._copy_text(self.listings[index].url, "Listing link copied.")

    def copy_all_links(self):
        ordered = [self.listings[int(row)] for row in self.tree.get_children()]
        self._copy_text(_links_text(ordered), f"{len(ordered)} links copied.")

    def show_qr(self):
        from PIL import ImageTk
        from tkinter import messagebox

        index = self.selected_index()
        if index is None:
            messagebox.showinfo("Select a listing", "Select one row first.", parent=self.window)
            return
        listing = self.listings[index]
        part = self.parts.get(listing.part_id)
        shop = self.shops.get(listing.shop_id)
        popup = self.tk.Toplevel(self.window)
        popup.title(f"Scan QR — {listing.part_id}")
        popup.resizable(False, False)
        self.ttk.Label(
            popup, text=part.display if part else listing.part_id,
            font=("Segoe UI", 12, "bold"), padding=(14, 12, 14, 2),
        ).pack()
        self.ttk.Label(
            popup, text=shop.name if shop else listing.shop_id,
            padding=(14, 0, 14, 4),
        ).pack()
        photo = ImageTk.PhotoImage(make_qr_image(listing.url, box_size=10))
        image_label = self.ttk.Label(popup, image=photo, padding=10)
        image_label.image = photo
        image_label.pack()
        self.ttk.Label(
            popup, text=f"Taobao item ID: {_item_id(listing.url) or 'unknown'}",
            padding=(14, 0, 14, 12),
        ).pack()

    def export_qr_sheet(self):
        ordered = [self.listings[int(row)] for row in self.tree.get_children()]
        sheet = export_qr_sheet(self.session.root, ordered, self.shops, self.parts)
        webbrowser.open(sheet.as_uri())
        self.status.configure(text=f"QR sheet exported: {sheet}")

    def verify_selected(self):
        from tkinter import messagebox, simpledialog

        index = self.selected_index()
        if index is None:
            messagebox.showinfo("Select a listing", "Select one row first.", parent=self.window)
            return
        listing = self.listings[index]
        sku = simpledialog.askstring(
            "Exact SKU / 规格",
            "Enter the exact Taobao variant:",
            initialvalue=listing.sku_note,
            parent=self.window,
        )
        if sku is None:
            return
        price = simpledialog.askfloat(
            "Actual price", "Price per lot (CNY):",
            initialvalue=listing.price, minvalue=0.0001, parent=self.window,
        )
        if price is None:
            return
        lot_size = simpledialog.askinteger(
            "Lot size", "Units received per lot:",
            initialvalue=listing.lot_size, minvalue=1, parent=self.window,
        )
        if lot_size is None:
            return
        reason = "item page manually verified"
        reasons = list(listing.match_reasons)
        if reason not in reasons:
            reasons.append(reason)
        self.listings[index] = dataclasses.replace(
            listing, sku_note=sku.strip(), price=price, lot_size=lot_size,
            verified=True, match_reasons=reasons,
        )
        save_listings(
            self.session.listings, list(self.shops.values()), self.listings
        )
        self.regenerate_plan(show_message=False)
        self.reload()
        self.status.configure(text=f"Verified {listing.part_id}.")

    def toggle_cart(self):
        index = self.selected_index()
        if index is None:
            return
        key = _state_key(self.listings[index])
        self.cart_state[key] = not self.cart_state.get(key, False)
        self.save_cart_state()
        self.reload()

    def regenerate_plan(self, show_message: bool = True):
        from tkinter import messagebox

        try:
            plan = solve(
                list(self.parts.values()), self.shops, self.listings,
                max_shops=self.session.max_shops,
            )
            text = render_plan(plan)
            self.session.plan.write_text(text, encoding="utf-8")
            self.session.plan.with_suffix(".md").write_text(text, encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("Could not generate plan", str(exc), parent=self.window)
            return
        if show_message:
            self.reload()
            messagebox.showinfo(
                "Plan updated",
                f"{len(plan.assignments)} items · {len(plan.by_shop())} shops · "
                f"¥{plan.total:.2f}",
                parent=self.window,
            )

    def open_folder(self):
        if hasattr(os, "startfile"):
            os.startfile(self.session.root)
        else:
            webbrowser.open(self.session.root.as_uri())

    def run(self):
        self.window.mainloop()


def search_session(session_path: str | Path) -> int:
    from .scrape import scrape

    session = load_session(session_path)
    parts = load_bom(session.bom)
    if session.overrides:
        overrides = json.loads(session.overrides.read_text(encoding="utf-8"))
        parts = apply_overrides(parts, overrides)
    shops_list, candidates = scrape(
        parts, session.root / "raw", per_part=20, retries=2, delay=6.0
    )
    if not candidates:
        print("No listings were scraped. Check login state and raw diagnostics.")
        return 1
    all_shops = {shop.id: shop for shop in shops_list}
    save_listings(session.root / "candidates.json", shops_list, candidates)

    plan = None
    first_cap = max(1, session.max_shops)
    for cap in range(first_cap, max(first_cap, len(parts)) + 1):
        try:
            plan = solve(parts, all_shops, candidates, max_shops=cap)
            break
        except RuntimeError:
            continue
    if plan is None:
        print("Candidate listings could not form a feasible shopping plan.")
        return 2
    selected = [assignment.listing for assignment in plan.assignments]
    used_shop_ids = {listing.shop_id for listing in selected}
    save_listings(
        session.listings,
        [all_shops[shop_id] for shop_id in used_shop_ids],
        selected,
    )
    text = render_plan(plan)
    session.plan.write_text(text, encoding="utf-8")
    session.plan.with_suffix(".md").write_text(text, encoding="utf-8")
    print(
        f"Selected {len(selected)} listings from {len(used_shop_ids)} shops; "
        f"{len(plan.unmatched)} unmatched; estimated CNY {plan.total:.2f}"
    )
    return 0


def run_gui(session_path: str | Path | None = None) -> int:
    configure_windows_app_identity()
    if session_path is None:
        selected = FirstBootLauncher().run()
        if selected is None:
            return 0
        session_path = selected
    remember_session(Path(session_path).resolve())
    session = load_session(session_path)
    TaobomGui(session).run()
    return 0
