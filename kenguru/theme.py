"""
kenguru.theme
~~~~~~~~~~~~~
Colour tokens, ttk style application, and widget recolouring helpers.

Single fixed palette — optimised for in-vehicle use day and night:
  · Near-black background avoids halation from pure black
  · Amber accent (rod-cell safe) for primary interactive elements
  · Green accent for secondary / status elements
  · Warm white text — less harsh than cool white at night
  · No blue — the eye's night-vision rod cells are almost blind to it
"""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk

# ── In-vehicle palette ────────────────────────────────────────────
DARK_BG  = "#0a0a0a"   # near-black background
PANEL_BG = "#141414"   # panel / field background
ACCENT   = "#e8a020"   # amber — primary accent, aviation / motorsport standard
ACCENT2  = "#4ec94e"   # green — secondary accent, night-vision safe
TEXT_FG  = "#e8e0c8"   # warm white — readable at night without glare
MUTED    = "#404040"   # dark grey — borders and de-emphasised text
ROW_ODD  = "#111111"   # slightly lighter than background for row scanning
ROW_EVEN = "#0a0a0a"   # same as background
SEL_BG   = "#2a1f00"   # dark amber tint — obvious selection, not glaring


def apply_theme(root: tk.Tk, prefs: dict, tree_font_size: int,
                status_label: tk.Label, rec_stats_label: tk.Label) -> None:
    """Configure all ttk styles and recolour native tk widgets."""
    root.configure(bg=DARK_BG)
    root.option_add("*Font", "Consolas 10")

    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=DARK_BG, foreground=TEXT_FG,
                fieldbackground=PANEL_BG, bordercolor=MUTED,
                troughcolor=PANEL_BG, selectbackground=ACCENT,
                selectforeground=DARK_BG, font=("Consolas", 10))
    s.configure("Treeview", background=ROW_EVEN, foreground=TEXT_FG,
                fieldbackground=ROW_EVEN,
                rowheight=int(tree_font_size * 2.25),
                borderwidth=0, font=("Consolas", tree_font_size))
    s.configure("Treeview.Heading", background=PANEL_BG, foreground=ACCENT,
                relief="flat", font=("Consolas", tree_font_size, "bold"))
    s.map("Treeview",
          background=[("selected", SEL_BG)],
          foreground=[("selected", TEXT_FG)])
    s.configure("TButton", background=ACCENT, foreground=DARK_BG,
                relief="flat", padding=[10, 5], font=("Consolas", 10, "bold"))
    s.map("TButton", background=[("active", ACCENT2)],
                     foreground=[("active", DARK_BG)])
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
          foreground=[("selected", DARK_BG)])
    # Function buttons — green tint
    s.configure("FButton.TButton", background="#1a3a1a", foreground=ACCENT2,
                relief="flat", padding=[10, 5], font=("Consolas", 10, "bold"))
    s.map("FButton.TButton",
          background=[("active", "#2a5a2a")],
          foreground=[("active", "#ffffff")])
    # Start recording — green
    s.configure("Start.TButton", background="#1a4a1a", foreground=ACCENT2,
                relief="flat", padding=[10, 5], font=("Consolas", 10, "bold"))
    s.map("Start.TButton", background=[("active", "#2a6a2a")])
    # Stop recording — red, kept bright for emergency visibility
    s.configure("Stop.TButton", background="#6a0000", foreground="#ffffff",
                relief="flat", padding=[10, 5], font=("Consolas", 10, "bold"))
    s.map("Stop.TButton", background=[("active", "#8a0000")])

    status_label.configure(bg="#1a4a1a", fg=ACCENT2)
    rec_stats_label.configure(bg=DARK_BG, fg=TEXT_FG)

    recolor_tk_widgets(root, skip={str(status_label), str(rec_stats_label)})


def recolor_tk_widgets(widget: tk.Widget = None,
                       skip: set | None = None) -> None:
    """Walk *widget*'s subtree and recolour native (non-ttk) widgets.

    ttk widgets are styled through ttk.Style; this handles tk.Frame,
    tk.Label, tk.Canvas, tk.Listbox, tk.Button, tk.Text, tk.Scrollbar.
    Widgets whose string ID is in *skip* are left untouched.
    """
    if skip is None:
        skip = set()

    def _visit(w: tk.Widget) -> None:
        if str(w) in skip:
            return
        cls = type(w).__name__
        try:
            if cls == "Frame":
                w.configure(bg=DARK_BG)
            elif cls == "Label":
                w.configure(bg=DARK_BG, fg=TEXT_FG)
            elif cls == "Canvas":
                w.configure(bg=DARK_BG)
            elif cls == "Listbox":
                w.configure(bg=PANEL_BG, fg=TEXT_FG, selectbackground=SEL_BG)
            elif cls == "Button":
                w.configure(bg=PANEL_BG, fg=TEXT_FG,
                            activebackground=ACCENT, activeforeground=DARK_BG)
            elif cls == "Text":
                w.configure(bg=PANEL_BG, fg=TEXT_FG, insertbackground=TEXT_FG)
            elif cls == "Scrollbar":
                w.configure(bg=PANEL_BG, troughcolor=DARK_BG)
        except tk.TclError:
            pass
        for child in w.winfo_children():
            _visit(child)

    _visit(widget)
