"""
kenguru.preferences
~~~~~~~~~~~~~~~~~~~
PreferencesManager: default values, JSON persistence, and the Preferences
Toplevel dialog (General tab only — colours are fixed in theme.py).
"""
from __future__ import annotations

import json
import os
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, ttk
from typing import Callable

from . import theme as th

# Keys that live only in RAM and must not be written to disk or exported.
PREFS_SKIP: frozenset[str] = frozenset({"filename_counter"})

# Chunk duration options shown in the dialog: label → seconds (0 = off)
CHUNK_OPTIONS: dict[str, int] = {
    "Off":        0,
    "1 minute":   60,
    "5 minutes":  300,
    "10 minutes": 600,
    "30 minutes": 1800,
}
# Reverse map for populating the dropdown from a saved integer value
_CHUNK_LABEL: dict[int, str] = {v: k for k, v in CHUNK_OPTIONS.items()}

_DEFAULT_PREFS: dict = {
    "save_dir":         os.path.join(os.path.expanduser("~"), "Documents", "CAN_Logs"),
    "filename_mode":    "timestamp",
    "filename_prefix":  "CAN_Record",
    "filename_counter": 1,
    "chunk_duration":   0,          # seconds; 0 = disabled
    "meta_project":     "",
    "meta_vehicle":     "",
    "meta_driver":      "",
    "meta_config":      "",
    "meta_comment":     "",
    "f1_label":         "F1",
    "f1_exe":           "",
    "f2_label":         "F2",
    "f2_exe":           "",
    "f3_label":         "F3",
    "f3_exe":           "",
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
            pass

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
        """Open the Preferences Toplevel."""
        win = tk.Toplevel(parent)
        win.title("Preferences")
        win.resizable(True, False)
        win.configure(bg=th.DARK_BG)
        win.grab_set()

        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        # ════════════════════════════════════════════════════════════
        #  TAB — General
        # ════════════════════════════════════════════════════════════
        gen_tab = ttk.Frame(nb)
        nb.add(gen_tab, text="  General  ")

        # ── Save directory ───────────────────────────────────────────
        dir_frame = ttk.LabelFrame(gen_tab, text="Recording Save Directory", padding=8)
        dir_frame.pack(fill="x", padx=12, pady=(12, 4))

        dir_var   = tk.StringVar(value=self.prefs["save_dir"])
        dir_entry = ttk.Entry(dir_frame, textvariable=dir_var, width=52)
        dir_entry.grid(row=0, column=0, padx=(0, 6), pady=4, sticky="we")

        def browse_dir():
            chosen = filedialog.askdirectory(initialdir=dir_var.get())
            if chosen:
                dir_var.set(chosen)

        ttk.Button(dir_frame, text="Browse...", command=browse_dir).grid(
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
        ttk.Label(name_frame, text="next #:").grid(
            row=1, column=2, padx=(8, 2), pady=2)
        ttk.Spinbox(name_frame, textvariable=counter_var,
                    from_=1, to=99999, width=7).grid(row=1, column=3, pady=2)
        ttk.Label(name_frame, text=".blf)").grid(
            row=1, column=4, padx=(2, 0), pady=2)

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

        # ── Chunk recording ──────────────────────────────────────────
        chunk_frame = ttk.LabelFrame(gen_tab, text="Chunk Recording", padding=8)
        chunk_frame.pack(fill="x", padx=12, pady=4)

        ttk.Label(chunk_frame, text="Split recording every:").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=4)

        current_label = _CHUNK_LABEL.get(self.prefs["chunk_duration"], "Off")
        chunk_var = tk.StringVar(value=current_label)
        ttk.Combobox(
            chunk_frame, textvariable=chunk_var,
            values=list(CHUNK_OPTIONS.keys()),
            state="readonly", width=12,
        ).grid(row=0, column=1, sticky="w", pady=4)

        ttk.Label(
            chunk_frame,
            text="Each chunk gets its own BLF + video file.\n"
                 "Files are named automatically with a timestamp or counter suffix.",
            foreground=th.MUTED, font=("Consolas", 9),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 4))

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

            ttk.Button(fn_frame, text="...", width=2,
                       command=_browse_exe).grid(
                row=n - 1, column=4, padx=(4, 0), pady=3)
        fn_frame.columnconfigure(3, weight=1)

        # ════════════════════════════════════════════════════════════
        #  Bottom bar — OK / Cancel
        # ════════════════════════════════════════════════════════════
        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill="x", pady=(4, 10), padx=8)

        def apply_and_close() -> None:
            self.prefs["save_dir"]         = dir_var.get().strip() or self.prefs["save_dir"]
            self.prefs["filename_mode"]    = mode_var.get()
            self.prefs["filename_prefix"]  = prefix_var.get().strip() or "CAN_Record"
            self.prefs["filename_counter"] = counter_var.get()
            self.prefs["chunk_duration"]   = CHUNK_OPTIONS.get(chunk_var.get(), 0)
            for key, var in si_vars.items():
                self.prefs[key] = var.get().strip()
            self.prefs["meta_comment"] = comment_text.get("1.0", "end-1c").strip()
            for key, var in fn_vars.items():
                self.prefs[key] = var.get().strip()
            self.save()
            on_apply()
            win.destroy()

        ttk.Button(btn_frame, text="OK",     command=apply_and_close).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="Cancel", command=win.destroy).pack(side="left", padx=2)
