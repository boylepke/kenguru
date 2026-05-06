"""
kenguru.theme
~~~~~~~~~~~~~
Colour tokens, ttk style application, and widget recolouring helpers.

Keeping these together means a single import gives any dialog access to
the active palette without importing the whole application.
"""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk

# ── Default palette (Monokai-inspired dark) ───────────────────────
DARK_BG  = "#1e1e2e"
PANEL_BG = "#2a2a3e"
ACCENT   = "#7c6af7"
ACCENT2  = "#48cfad"
TEXT_FG  = "#cdd6f4"
MUTED    = "#6c7086"
ROW_ODD  = "#242436"
ROW_EVEN = "#1e1e2e"
SEL_BG   = "#45475a"

# Colour presets available in the Preferences dialog.
PRESETS: dict[str, dict[str, str]] = {
    "Default Dark": {
        "color_dark_bg": "#1e1e2e", "color_panel_bg": "#2a2a3e",
        "color_accent":  "#7c6af7", "color_accent2":  "#48cfad",
        "color_text_fg": "#cdd6f4", "color_muted":    "#6c7086",
        "color_row_odd": "#242436", "color_row_even": "#1e1e2e",
        "color_sel_bg":  "#45475a",
    },
    "Midnight Blue": {
        "color_dark_bg": "#0d1117", "color_panel_bg": "#161b22",
        "color_accent":  "#58a6ff", "color_accent2":  "#3fb950",
        "color_text_fg": "#c9d1d9", "color_muted":    "#484f58",
        "color_row_odd": "#161b22", "color_row_even": "#0d1117",
        "color_sel_bg":  "#1f6feb",
    },
    "Light": {
        "color_dark_bg": "#f5f5f5", "color_panel_bg": "#e8e8e8",
        "color_accent":  "#5b4fcf", "color_accent2":  "#2a9d8f",
        "color_text_fg": "#1a1a2e", "color_muted":    "#888888",
        "color_row_odd": "#eeeeee", "color_row_even": "#f5f5f5",
        "color_sel_bg":  "#c5b8ff",
    },
    "Matrix": {
        "color_dark_bg": "#060606", "color_panel_bg": "#0d1a0d",
        "color_accent":  "#00e536", "color_accent2":  "#39ff14",
        "color_text_fg": "#b3ffb3", "color_muted":    "#2e5c2e",
        "color_row_odd": "#0a160a", "color_row_even": "#060606",
        "color_sel_bg":  "#004d00",
    },
    "Sunset": {
        "color_dark_bg": "#160800", "color_panel_bg": "#251400",
        "color_accent":  "#ff6b00", "color_accent2":  "#ffd000",
        "color_text_fg": "#ffe8c8", "color_muted":    "#7a5020",
        "color_row_odd": "#1f1000", "color_row_even": "#160800",
        "color_sel_bg":  "#5c2500",
    },
    "Cyberpunk": {
        "color_dark_bg": "#0d0015", "color_panel_bg": "#1a0030",
        "color_accent":  "#ff00cc", "color_accent2":  "#00e5ff",
        "color_text_fg": "#e8ccff", "color_muted":    "#5c2e7a",
        "color_row_odd": "#140020", "color_row_even": "#0d0015",
        "color_sel_bg":  "#3d006b",
    },
}


def sync_globals(prefs: dict) -> None:
    """Copy colour values from *prefs* into the module-level constants.

    Any module that does ``from kenguru.theme import ACCENT`` will see the
    updated value after this call because they share the same module object.
    """
    global DARK_BG, PANEL_BG, ACCENT, ACCENT2, TEXT_FG, MUTED, ROW_ODD, ROW_EVEN, SEL_BG
    DARK_BG  = prefs["color_dark_bg"]
    PANEL_BG = prefs["color_panel_bg"]
    ACCENT   = prefs["color_accent"]
    ACCENT2  = prefs["color_accent2"]
    TEXT_FG  = prefs["color_text_fg"]
    MUTED    = prefs["color_muted"]
    ROW_ODD  = prefs["color_row_odd"]
    ROW_EVEN = prefs["color_row_even"]
    SEL_BG   = prefs["color_sel_bg"]


def apply_theme(root: tk.Tk, prefs: dict, tree_font_size: int,
                status_label: tk.Label, rec_stats_label: tk.Label) -> None:
    """Configure all ttk styles from *prefs* and recolour native tk widgets."""
    sync_globals(prefs)

    root.configure(bg=DARK_BG)
    root.option_add("*Font", "Consolas 10")

    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=DARK_BG, foreground=TEXT_FG,
                fieldbackground=PANEL_BG, bordercolor=MUTED,
                troughcolor=PANEL_BG, selectbackground=ACCENT,
                selectforeground=TEXT_FG, font=("Consolas", 10))
    s.configure("Treeview", background=ROW_EVEN, foreground=TEXT_FG,
                fieldbackground=ROW_EVEN,
                rowheight=int(tree_font_size * 2.25),
                borderwidth=0, font=("Consolas", tree_font_size))
    s.configure("Treeview.Heading", background=PANEL_BG, foreground=ACCENT,
                relief="flat", font=("Consolas", tree_font_size, "bold"))
    s.map("Treeview",
          background=[("selected", SEL_BG)],
          foreground=[("selected", TEXT_FG)])
    s.configure("TButton", background=ACCENT, foreground="#ffffff",
                relief="flat", padding=[10, 5], font=("Consolas", 10, "bold"))
    s.map("TButton", background=[("active", ACCENT)])
    s.configure("TLabel",      background=DARK_BG,  foreground=TEXT_FG)
    s.configure("TEntry",      fieldbackground=PANEL_BG, foreground=TEXT_FG,
                insertcolor=TEXT_FG, borderwidth=0, relief="flat")
    s.configure("TFrame",      background=DARK_BG)
    s.configure("TLabelframe", background=DARK_BG,  foreground=ACCENT,
                bordercolor=MUTED)
    s.configure("TLabelframe.Label", background=DARK_BG, foreground=ACCENT,
                font=("Consolas", 10, "bold"))
    s.configure("TCombobox",   fieldbackground=PANEL_BG, foreground=TEXT_FG,
                selectbackground=ACCENT, background=PANEL_BG)
    s.map("TCombobox",
          fieldbackground=[("readonly", PANEL_BG)],
          foreground=[("readonly", TEXT_FG)])
    s.configure("TScrollbar",  background=PANEL_BG, troughcolor=DARK_BG,
                borderwidth=0, arrowsize=12)
    s.configure("TSpinbox",    fieldbackground=PANEL_BG, foreground=TEXT_FG,
                background=PANEL_BG)
    s.configure("TNotebook",   background=DARK_BG, borderwidth=0)
    s.configure("TNotebook.Tab", background=PANEL_BG, foreground=TEXT_FG,
                padding=[10, 4], font=("Consolas", 10))
    s.map("TNotebook.Tab",
          background=[("selected", ACCENT)],
          foreground=[("selected", "#ffffff")])
    # Function buttons — subtle green
    s.configure("FButton.TButton", background="#2d6a4f", foreground="#d8f3dc",
                relief="flat", padding=[10, 5], font=("Consolas", 10, "bold"))
    s.map("FButton.TButton",
          background=[("active", "#40916c")],
          foreground=[("active", "#ffffff")])
    # Start / Stop recording — semantic colours
    s.configure("Start.TButton", background="#40916c", foreground="#ffffff",
                relief="flat", padding=[10, 5], font=("Consolas", 10, "bold"))
    s.map("Start.TButton", background=[("active", "#52b788")])
    s.configure("Stop.TButton", background="#c0392b", foreground="#ffffff",
                relief="flat", padding=[10, 5], font=("Consolas", 10, "bold"))
    s.map("Stop.TButton", background=[("active", "#e74c3c")])

    status_label.configure(bg="green", fg="#ffffff")
    rec_stats_label.configure(bg=DARK_BG, fg="#ffffff")

    recolor_tk_widgets(root, prefs, skip={str(status_label), str(rec_stats_label)})


def recolor_tk_widgets(widget: tk.Widget, prefs: dict,
                       skip: set | None = None) -> None:
    """Walk *widget*'s subtree and recolour native (non-ttk) widgets.

    ttk widgets are styled through ttk.Style; this handles tk.Frame,
    tk.Label, tk.Canvas, tk.Listbox, tk.Button, tk.Text, tk.Scrollbar.
    Widgets whose string ID is in *skip* are left untouched.
    """
    if skip is None:
        skip = set()
    bg  = prefs["color_dark_bg"]
    pbg = prefs["color_panel_bg"]
    fg  = prefs["color_text_fg"]
    sel = prefs["color_sel_bg"]
    acc = prefs["color_accent"]

    def _visit(w: tk.Widget) -> None:
        if str(w) in skip:
            return
        cls = type(w).__name__
        try:
            if cls == "Frame":
                w.configure(bg=bg)
            elif cls == "Label":
                w.configure(bg=bg, fg=fg)
            elif cls == "Canvas":
                w.configure(bg=bg)
            elif cls == "Listbox":
                w.configure(bg=pbg, fg=fg, selectbackground=sel)
            elif cls == "Button":
                w.configure(bg=pbg, fg=fg,
                            activebackground=acc, activeforeground="#ffffff")
            elif cls == "Text":
                w.configure(bg=pbg, fg=fg, insertbackground=fg)
            elif cls == "Scrollbar":
                w.configure(bg=pbg, troughcolor=bg)
        except tk.TclError:
            pass
        for child in w.winfo_children():
            _visit(child)

    _visit(widget)
