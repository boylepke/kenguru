"""
kenguru.preferences
~~~~~~~~~~~~~~~~~~~
PreferencesManager: default values, JSON persistence, and the Preferences
Toplevel dialog (General + Colors tabs).
"""
from __future__ import annotations

import json
import os
import tkinter as tk
from datetime import datetime
from tkinter import colorchooser, filedialog, ttk
from typing import Callable

from . import theme as th

# Keys that live only in RAM (runtime counters etc.) and must not be
# written to disk or exported.
PREFS_SKIP: frozenset[str] = frozenset({"filename_counter"})

_DEFAULT_PREFS: dict = {
    "save_dir":          os.path.join(os.path.expanduser("~"), "Documents", "CAN_Logs"),
    "filename_mode":     "timestamp",
    "filename_prefix":   "CAN_Record",
    "filename_counter":  1,
    "meta_project":      "",
    "meta_vehicle":      "",
    "meta_driver":       "",
    "meta_config":       "",
    "meta_comment":      "",
    "f1_label":          "F1",
    "f1_exe":            "",
    "f2_label":          "F2",
    "f2_exe":            "",
    "f3_label":          "F3",
    "f3_exe":            "",
    # ── Colours ──────────────────────────────────────────────────────
    "color_dark_bg":     "#1e1e2e",
    "color_panel_bg":    "#2a2a3e",
    "color_accent":      "#7c6af7",
    "color_accent2":     "#48cfad",
    "color_text_fg":     "#cdd6f4",
    "color_muted":       "#6c7086",
    "color_row_odd":     "#242436",
    "color_row_even":    "#1e1e2e",
    "color_sel_bg":      "#45475a",
}


class PreferencesManager:
    """Owns the application preferences dict and its JSON persistence."""

    def __init__(self) -> None:
        self.prefs: dict = dict(_DEFAULT_PREFS)
        self.load()

    # ── Persistence ──────────────────────────────────────────────────

    @staticmethod
    def _prefs_path() -> str:
        base   = os.environ.get("APPDATA") or os.path.expanduser("~")
        folder = os.path.join(base, "KenguruCANMonitor")
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, "prefs.json")

    def load(self) -> None:
        """Load saved preferences from disk; silently use defaults if absent."""
        try:
            with open(self._prefs_path(), "r", encoding="utf-8") as f:
                saved = json.load(f)
            for k in self.prefs:
                if k in saved and k not in PREFS_SKIP:
                    self.prefs[k] = saved[k]
        except (FileNotFoundError, Exception):
            pass  # first run or corrupt file — use defaults

    def save(self) -> None:
        """Persist current preferences to disk (non-fatal on failure)."""
        exportable = {k: v for k, v in self.prefs.items() if k not in PREFS_SKIP}
        try:
            with open(self._prefs_path(), "w", encoding="utf-8") as f:
                json.dump(exportable, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def get(self, key: str, default=None):
        return self.prefs.get(key, default)

    def __getitem__(self, key: str):
        return self.prefs[key]

    def __setitem__(self, key: str, value) -> None:
        self.prefs[key] = value

    # ── Config import / export ───────────────────────────────────────

    def export_config(self, parent: tk.Widget) -> None:
        path = filedialog.asksaveasfilename(
            parent=parent,
            title="Export configuration",
            defaultextension=".json",
            filetypes=[("JSON config", "*.json"), ("All files", "*.*")],
            initialfile="kenguru_config.json",
        )
        if not path:
            return
        exportable = {k: v for k, v in self.prefs.items() if k not in PREFS_SKIP}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(exportable, f, indent=2, ensure_ascii=False)
            tk.messagebox.showinfo("Exported", f"Configuration saved to:\n{path}", parent=parent)
        except Exception as e:
            tk.messagebox.showerror("Export failed", f"Could not write file:\n{e}", parent=parent)

    def import_config(self, parent: tk.Widget) -> None:
        path = filedialog.askopenfilename(
            parent=parent,
            title="Import configuration",
            filetypes=[("JSON config", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except Exception as e:
            tk.messagebox.showerror("Import failed", f"Could not read file:\n{e}", parent=parent)
            return
        for k in self.prefs:
            if k in loaded and k not in PREFS_SKIP:
                self.prefs[k] = loaded[k]
        tk.messagebox.showinfo(
            "Imported",
            f"Configuration loaded from:\n{path}\n\n"
            "Open Preferences to review the imported settings.",
            parent=parent,
        )

    # ── Preferences dialog ───────────────────────────────────────────

    def open_dialog(
        self,
        parent: tk.Widget,
        on_apply: Callable[[], None],
    ) -> None:
        """Open the Preferences Toplevel.

        Parameters
        ----------
        parent:
            Owner window (used for grab_set positioning).
        on_apply:
            Callback fired after the user clicks OK so the caller can
            re-apply the theme and refresh any dependent widgets.
        """
        win = tk.Toplevel(parent)
        win.title("Preferences")
        win.resizable(True, False)
        win.configure(bg=th.DARK_BG)
        win.grab_set()

        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        # ════════════════════════════════════════════════════════════
        #  TAB 1 — General
        # ════════════════════════════════════════════════════════════
        gen_tab = ttk.Frame(nb)
        nb.add(gen_tab, text="  General  ")

        # ── Save directory ───────────────────────────────────────────
        dir_frame = ttk.LabelFrame(gen_tab, text="Recording Save Directory", padding=8)
        dir_frame.pack(fill="x", padx=12, pady=(12, 4))

        dir_var = tk.StringVar(value=self.prefs["save_dir"])
        dir_entry = ttk.Entry(dir_frame, textvariable=dir_var, width=52)
        dir_entry.grid(row=0, column=0, padx=(0, 6), pady=4, sticky="we")

        def browse_dir():
            chosen = filedialog.askdirectory(initialdir=dir_var.get())
            if chosen:
                dir_var.set(chosen)

        ttk.Button(dir_frame, text="Browse…", command=browse_dir).grid(
            row=0, column=1, pady=4)
        dir_frame.columnconfigure(0, weight=1)

        # ── File naming ──────────────────────────────────────────────
        name_frame = ttk.LabelFrame(gen_tab, text="File Naming", padding=8)
        name_frame.pack(fill="x", padx=12, pady=4)

        mode_var = tk.StringVar(value=self.prefs["filename_mode"])
        ttk.Radiobutton(
            name_frame, text="Timestamp  (e.g. CAN_Record_20250601_143012.blf)",
            variable=mode_var, value="timestamp",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=2)

        ttk.Radiobutton(
            name_frame, text="Prefix + counter  (e.g.",
            variable=mode_var, value="prefix_counter",
        ).grid(row=1, column=0, sticky="w", pady=2)

        prefix_var  = tk.StringVar(value=self.prefs["filename_prefix"])
        counter_var = tk.IntVar(value=self.prefs["filename_counter"])
        ttk.Entry(name_frame, textvariable=prefix_var, width=18).grid(
            row=1, column=1, padx=4, pady=2)
        ttk.Label(name_frame, text="next #:").grid(row=1, column=2, padx=(8, 2), pady=2)
        ttk.Spinbox(name_frame, textvariable=counter_var,
                    from_=1, to=99999, width=7).grid(row=1, column=3, pady=2)
        ttk.Label(name_frame, text=".blf)").grid(row=1, column=4, padx=(2, 0), pady=2)

        preview_var = tk.StringVar()

        def update_preview(*_):
            prefix = prefix_var.get().strip() or "CAN_Record"
            if mode_var.get() == "timestamp":
                preview_var.set(
                    f"Preview:  {prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.blf")
            else:
                n = counter_var.get()
                preview_var.set(f"Preview:  {prefix}_{n:04d}.blf")

        mode_var.trace_add("write", update_preview)
        prefix_var.trace_add("write", update_preview)
        counter_var.trace_add("write", update_preview)
        update_preview()

        ttk.Label(name_frame, textvariable=preview_var,
                  foreground=th.ACCENT2).grid(
            row=2, column=0, columnspan=5, sticky="w", pady=(6, 2))

        # ── Session Info Template ────────────────────────────────────
        si_frame = ttk.LabelFrame(
            gen_tab,
            text="Session Info Template  (saved alongside every BLF)",
            padding=8,
        )
        si_frame.pack(fill="x", padx=12, pady=4)

        si_fields = [
            ("Project:",       "meta_project"),
            ("Vehicle:",       "meta_vehicle"),
            ("Driver:",        "meta_driver"),
            ("Configuration:", "meta_config"),
        ]
        si_vars: dict[str, tk.StringVar] = {}
        for row_i, (label, key) in enumerate(si_fields):
            ttk.Label(si_frame, text=label).grid(
                row=row_i, column=0, sticky="w", padx=(0, 6), pady=3)
            v = tk.StringVar(value=self.prefs[key])
            si_vars[key] = v
            ttk.Entry(si_frame, textvariable=v, width=50).grid(
                row=row_i, column=1, sticky="we", pady=3)
        si_frame.columnconfigure(1, weight=1)

        ttk.Label(si_frame, text="Comment:").grid(
            row=len(si_fields), column=0, sticky="nw", padx=(0, 6), pady=3)
        comment_text = tk.Text(si_frame, height=3, width=50,
                               bg=th.PANEL_BG, fg=th.TEXT_FG,
                               insertbackground=th.TEXT_FG,
                               relief="flat", font=("Consolas", 10))
        comment_text.insert("1.0", self.prefs["meta_comment"])
        comment_text.grid(row=len(si_fields), column=1, sticky="we", pady=3)

        # ── Function Buttons ─────────────────────────────────────────
        fn_frame = ttk.LabelFrame(
            gen_tab, text="Function Buttons  (F1 / F2 / F3)", padding=8)
        fn_frame.pack(fill="x", padx=12, pady=4)

        fn_vars: dict[str, tk.StringVar] = {}
        for n in (1, 2, 3):
            ttk.Label(fn_frame, text=f"F{n} label:").grid(
                row=n - 1, column=0, sticky="w", padx=(0, 6), pady=3)
            lbl_v = tk.StringVar(value=self.prefs[f"f{n}_label"])
            fn_vars[f"f{n}_label"] = lbl_v
            ttk.Entry(fn_frame, textvariable=lbl_v, width=14).grid(
                row=n - 1, column=1, padx=(0, 12), pady=3)

            ttk.Label(fn_frame, text="Executable:").grid(
                row=n - 1, column=2, sticky="w", padx=(0, 6), pady=3)
            exe_v = tk.StringVar(value=self.prefs[f"f{n}_exe"])
            fn_vars[f"f{n}_exe"] = exe_v
            ttk.Entry(fn_frame, textvariable=exe_v, width=42).grid(
                row=n - 1, column=3, sticky="we", pady=3)

            def _browse_exe(var=exe_v):
                p = filedialog.askopenfilename(
                    title="Select executable",
                    filetypes=[("Executables", "*.exe *.bat *.cmd"),
                               ("All files", "*.*")],
                )
                if p:
                    var.set(p)

            ttk.Button(fn_frame, text="…", width=2,
                       command=_browse_exe).grid(
                row=n - 1, column=4, padx=(4, 0), pady=3)
        fn_frame.columnconfigure(3, weight=1)

        # ════════════════════════════════════════════════════════════
        #  TAB 2 — Colors
        # ════════════════════════════════════════════════════════════
        col_tab = ttk.Frame(nb)
        nb.add(col_tab, text="  Colors  ")

        COLOR_SLOTS = [
            ("Background",        "color_dark_bg"),
            ("Panel / Fields",    "color_panel_bg"),
            ("Accent (primary)",  "color_accent"),
            ("Accent (secondary)","color_accent2"),
            ("Text",              "color_text_fg"),
            ("Muted / Borders",   "color_muted"),
            ("Row (odd)",         "color_row_odd"),
            ("Row (even)",        "color_row_even"),
            ("Selection",         "color_sel_bg"),
        ]
        color_vars = {key: tk.StringVar(value=self.prefs[key])
                      for _, key in COLOR_SLOTS}

        # ── Preset buttons ───────────────────────────────────────────
        preset_frame = ttk.LabelFrame(col_tab, text="Presets", padding=8)
        preset_frame.pack(fill="x", padx=12, pady=(12, 4))

        def apply_preset(preset_name: str) -> None:
            p = th.PRESETS[preset_name]
            for key, var in color_vars.items():
                if key in p:
                    var.set(p[key])
            _refresh_swatches()

        for name in th.PRESETS:
            ttk.Button(preset_frame, text=name,
                       command=lambda n=name: apply_preset(n)).pack(
                side="left", padx=4, pady=2)

        # ── Colour slots grid ────────────────────────────────────────
        slots_frame = ttk.LabelFrame(col_tab, text="Individual Colours", padding=10)
        slots_frame.pack(fill="x", padx=12, pady=4)

        swatch_buttons: dict[str, tk.Button] = {}

        def _pick_color(key: str) -> None:
            result = colorchooser.askcolor(
                color=color_vars[key].get(), title="Choose colour", parent=win)
            if result and result[1]:
                color_vars[key].set(result[1].lower())
                _refresh_swatches()

        def _refresh_swatches() -> None:
            for key, btn in swatch_buttons.items():
                hex_val = color_vars[key].get()
                try:
                    r = int(hex_val[1:3], 16)
                    g = int(hex_val[3:5], 16)
                    b = int(hex_val[5:7], 16)
                    brightness = (r * 299 + g * 587 + b * 114) / 1000
                    fg_txt = "#000000" if brightness > 128 else "#ffffff"
                except Exception:
                    fg_txt = "#ffffff"
                btn.configure(bg=hex_val, fg=fg_txt,
                              activebackground=hex_val, activeforeground=fg_txt,
                              text=hex_val)

        for row_i, (label, key) in enumerate(COLOR_SLOTS):
            ttk.Label(slots_frame, text=label, width=20, anchor="w").grid(
                row=row_i, column=0, sticky="w", padx=(0, 10), pady=4)
            swatch = tk.Button(
                slots_frame, text=color_vars[key].get(),
                width=12, relief="flat", cursor="hand2",
                font=("Consolas", 9, "bold"),
                command=lambda k=key: _pick_color(k),
            )
            swatch.grid(row=row_i, column=1, padx=4, pady=4, sticky="w")
            swatch_buttons[key] = swatch
            ttk.Entry(slots_frame, textvariable=color_vars[key],
                      width=10).grid(row=row_i, column=2,
                                     padx=(4, 0), pady=4, sticky="w")
            color_vars[key].trace_add("write", lambda *_, k=key: _refresh_swatches())

        _refresh_swatches()

        ttk.Label(col_tab,
                  text="Changes apply immediately when you click OK.  "
                       "Restart is not required.",
                  foreground=th.MUTED, font=("Consolas", 9),
                  ).pack(padx=12, pady=(6, 2), anchor="w")

        # ════════════════════════════════════════════════════════════
        #  Bottom bar — OK / Cancel
        # ════════════════════════════════════════════════════════════
        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill="x", pady=(4, 10), padx=8)

        def apply_and_close() -> None:
            # General tab
            self.prefs["save_dir"]         = dir_var.get().strip() or self.prefs["save_dir"]
            self.prefs["filename_mode"]    = mode_var.get()
            self.prefs["filename_prefix"]  = prefix_var.get().strip() or "CAN_Record"
            self.prefs["filename_counter"] = counter_var.get()
            for key, var in si_vars.items():
                self.prefs[key] = var.get().strip()
            self.prefs["meta_comment"] = comment_text.get("1.0", "end-1c").strip()
            for key, var in fn_vars.items():
                self.prefs[key] = var.get().strip()
            # Colors tab
            for key, var in color_vars.items():
                self.prefs[key] = var.get().strip() or self.prefs[key]
            self.save()
            on_apply()
            win.destroy()

        ttk.Button(btn_frame, text="OK",     command=apply_and_close).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="Cancel", command=win.destroy).pack(side="left", padx=2)
