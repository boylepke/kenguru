"""
kenguru.signal_selector
~~~~~~~~~~~~~~~~~~~~~~~
Standalone signal-selection Toplevel.

Call ``open_signal_selector(app)`` to open the dialog.  On OK it updates
``app.session.selected_signals``, rebuilds the frame lookup, and refreshes
the live signal tree.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from . import theme as th


def open_signal_selector(app) -> None:
    """Open the Select Signals dialog for *app*."""
    session = app.session
    if not session.dbs:
        messagebox.showwarning("Warning", "Load at least one DBC first.")
        return

    window = tk.Toplevel(app.root)
    window.title("Select Signals")
    window.geometry("940x580")
    window.configure(bg=th.DARK_BG)

    # ── Top bar: filter + select-all / clear-all ───────────────────
    top_bar = ttk.Frame(window, padding=(6, 4))
    top_bar.pack(fill="x")

    ttk.Label(top_bar, text="Filter:").pack(side="left")
    search_var = tk.StringVar()
    ttk.Entry(top_bar, textvariable=search_var, width=30).pack(
        side="left", padx=(4, 16))

    ttk.Button(top_bar, text="Select All (tab)",
               command=lambda: _set_all(True)).pack(side="left", padx=2)
    ttk.Button(top_bar, text="Clear All (tab)",
               command=lambda: _set_all(False)).pack(side="left", padx=2)

    selected_count_var = tk.StringVar(value="")
    ttk.Label(top_bar, textvariable=selected_count_var,
              foreground=th.ACCENT2).pack(side="right", padx=8)

    # ── Notebook — one tab per loaded DBC ─────────────────────────
    notebook = ttk.Notebook(window)
    notebook.pack(fill="both", expand=True, padx=6, pady=(0, 4))

    # Flat list across all tabs: (var, full_name, frame_id, sig_name,
    #   unit_var, decimals_var, scale_var, offset_var, row_widget, tab_rows_list)
    signal_controls: list = []
    tab_rows:        list[list] = []  # per-tab (var, full_name, row)

    def _make_column_header(parent: tk.Widget) -> None:
        hdr = tk.Frame(parent, bg=th.PANEL_BG)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Signal", width=38, anchor="w",
                 bg=th.PANEL_BG, fg=th.ACCENT,
                 font=("Consolas", 9, "bold")).pack(side="left", padx=2)
        for lbl, w in (("Dec", 4), ("Scale", 8), ("Offset", 8), ("Unit", 10)):
            tk.Label(hdr, text=lbl, width=w, anchor="center",
                     bg=th.PANEL_BG, fg=th.ACCENT,
                     font=("Consolas", 9, "bold")).pack(side="right", padx=2)

    for dbc_path, db in session.dbs:
        tab_label = os.path.basename(dbc_path)
        outer     = ttk.Frame(notebook)
        notebook.add(outer, text=tab_label)
        _make_column_header(outer)

        tab_canvas    = tk.Canvas(outer, bg=th.DARK_BG, highlightthickness=0)
        tab_scrollbar = ttk.Scrollbar(outer, orient="vertical",
                                      command=tab_canvas.yview)
        inner = tk.Frame(tab_canvas, bg=th.DARK_BG)

        inner.bind("<Configure>",
                   lambda e, c=tab_canvas: c.configure(scrollregion=c.bbox("all")))
        tab_canvas.create_window((0, 0), window=inner, anchor="nw")
        tab_canvas.configure(yscrollcommand=tab_scrollbar.set)
        tab_canvas.pack(side="left", fill="both", expand=True)
        tab_scrollbar.pack(side="right", fill="y")

        def _bind_wheel(canvas=tab_canvas) -> None:
            def _on_wheel(e):
                canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            canvas.bind("<MouseWheel>", _on_wheel)
        _bind_wheel()

        this_tab_rows: list = []
        tab_rows.append(this_tab_rows)

        for msg in db.messages:
            for sig in msg.signals:
                full_name = f"{msg.name}.{sig.name}"
                var       = tk.BooleanVar(value=(full_name in session.selected_signals))

                row     = tk.Frame(inner, bg=th.DARK_BG)
                row.pack(fill="x", anchor="w")
                row_bg  = th.ROW_ODD if len(this_tab_rows) % 2 else th.ROW_EVEN
                row.configure(bg=row_bg)

                cb = tk.Checkbutton(row, text=full_name, variable=var,
                                    bg=row_bg, fg=th.TEXT_FG,
                                    selectcolor=th.PANEL_BG,
                                    activebackground=row_bg,
                                    activeforeground=th.TEXT_FG,
                                    font=("Consolas", 9), anchor="w", width=36)
                cb.pack(side="left", padx=2)
                var.trace_add("write", lambda *_: _refresh_count())

                unit_default = sig.unit or ""
                unit_var = tk.StringVar(
                    value=session.selected_signals.get(full_name, {}).get("unit", unit_default))
                tk.Entry(row, textvariable=unit_var, width=10,
                         bg=th.PANEL_BG, fg=th.TEXT_FG, insertbackground=th.TEXT_FG,
                         relief="flat", font=("Consolas", 9)).pack(side="right", padx=2)

                offset_var = tk.DoubleVar(
                    value=session.selected_signals.get(full_name, {}).get("offset", 0.0))
                tk.Entry(row, textvariable=offset_var, width=8,
                         bg=th.PANEL_BG, fg=th.TEXT_FG, insertbackground=th.TEXT_FG,
                         relief="flat", font=("Consolas", 9)).pack(side="right", padx=2)

                scale_var = tk.DoubleVar(
                    value=session.selected_signals.get(full_name, {}).get("scale", 1.0))
                tk.Entry(row, textvariable=scale_var, width=8,
                         bg=th.PANEL_BG, fg=th.TEXT_FG, insertbackground=th.TEXT_FG,
                         relief="flat", font=("Consolas", 9)).pack(side="right", padx=2)

                decimals_var = tk.IntVar(
                    value=session.selected_signals.get(full_name, {}).get("decimals", 2))
                tk.Spinbox(row, textvariable=decimals_var, from_=0, to=6, width=4,
                           bg=th.PANEL_BG, fg=th.TEXT_FG, buttonbackground=th.PANEL_BG,
                           relief="flat", font=("Consolas", 9)).pack(side="right", padx=2)

                this_tab_rows.append((var, full_name, row))
                signal_controls.append((var, full_name, msg.frame_id, sig.name,
                                        unit_var, decimals_var, scale_var, offset_var,
                                        row, this_tab_rows))

    # ── Helpers ───────────────────────────────────────────────────
    def _current_tab_rows() -> list:
        idx = notebook.index(notebook.select())
        return tab_rows[idx] if idx < len(tab_rows) else []

    def _refresh_count() -> None:
        total = sum(1 for c in signal_controls if c[0].get())
        selected_count_var.set(f"{total} signal(s) selected")

    def _set_all(state: bool) -> None:
        term = search_var.get().lower()
        for var, full_name, row in _current_tab_rows():
            if not term or term in full_name.lower():
                var.set(state)

    def on_filter(*_) -> None:
        term = search_var.get().lower()
        for rows in tab_rows:
            for var, full_name, row in rows:
                if not term or term in full_name.lower():
                    row.pack(fill="x", anchor="w")
                else:
                    row.pack_forget()
        for tab_idx in range(notebook.index("end")):
            tab_frame = notebook.nametowidget(notebook.tabs()[tab_idx])
            for child in tab_frame.winfo_children():
                if isinstance(child, tk.Canvas):
                    child.configure(scrollregion=child.bbox("all"))

    search_var.trace_add("write", on_filter)
    _refresh_count()

    # ── Bottom bar ────────────────────────────────────────────────
    btn_bar = ttk.Frame(window, padding=(6, 4))
    btn_bar.pack(fill="x")

    def save() -> None:
        session.selected_signals.clear()
        for (var, full_name, frame_id, sig_name,
             unit_var, decimals_var, scale_var, offset_var, _, _trows) in signal_controls:
            if var.get():
                try:
                    scale = float(scale_var.get())
                except (ValueError, tk.TclError):
                    scale = 1.0
                try:
                    offset = float(offset_var.get())
                except (ValueError, tk.TclError):
                    offset = 0.0
                session.selected_signals[full_name] = {
                    "frame_id": frame_id,
                    "sig_name": sig_name,
                    "unit":     unit_var.get(),
                    "decimals": decimals_var.get(),
                    "scale":    scale,
                    "offset":   offset,
                }
        session.rebuild_frame_lookup()
        window.destroy()
        app.initialize_tree()

    ttk.Button(btn_bar, text="OK",     command=save).pack(side="left", padx=6)
    ttk.Button(btn_bar, text="Cancel", command=window.destroy).pack(side="left", padx=2)
