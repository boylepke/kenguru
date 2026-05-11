"""
kenguru.preferences
~~~~~~~~~~~~~~~~~~~
PreferencesManager: default values, JSON persistence, and the Preferences
Toplevel dialog.

Robocopy integration
~~~~~~~~~~~~~~~~~~~~
``build_robocopy_cmd()`` assembles the full command from stored prefs.
``run_robocopy()`` launches it in a detached subprocess — safe to call
from the receive loop thread after each chunk rotation.
"""
from __future__ import annotations

import json
import os
import subprocess
import traceback
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from . import theme as th

# ── Constants ────────────────────────────────────────────────────────
PREFS_SKIP: frozenset[str] = frozenset({"filename_counter"})

CHUNK_OPTIONS: dict[str, int] = {
    "Off":        0,
    "1 minute":   60,
    "5 minutes":  300,
    "10 minutes": 600,
    "30 minutes": 1800,
}
_CHUNK_LABEL: dict[int, str] = {v: k for k, v in CHUNK_OPTIONS.items()}

RCOPY_EXT_OPTIONS: list[tuple[str, str]] = [
    ("*.blf",      "BLF"),
    ("*.mf4",      "MF4"),
    ("*.csv",      "CSV"),
    ("*.avi",      "AVI"),
    ("*.mp4",      "MP4"),
    ("*.pts",      "PTS"),
    ("*.sync",     "SYNC"),
    ("*_info.txt", "TXT sidecar"),
]

_DEFAULT_PREFS: dict = {
    "save_dir":           os.path.join(os.path.expanduser("~"), "Documents", "CAN_Logs"),
    "filename_mode":      "timestamp",
    "filename_prefix":    "CAN_Record",
    "filename_counter":   1,
    "chunk_duration":     0,
    "rcopy_enabled":      False,
    "rcopy_src":          "",
    "rcopy_dst":          "",
    "rcopy_ext":          ["*.blf", "*.avi", "*.mp4", "*.pts", "*.sync", "*_info.txt"],
    "rcopy_mir":          False,
    "rcopy_z":            True,
    "rcopy_retries":      3,
    "rcopy_wait":         5,
    "rcopy_log":          False,
    "rcopy_log_path":     "",
    "rcopy_trigger":      "every",
    "rcopy_n_chunks":     5,
    "meta_project":       "",
    "meta_vehicle":       "",
    "meta_driver":        "",
    "meta_config":        "",
    "meta_comment":       "",
    "f1_label":           "F1",
    "f1_exe":             "",
    "f2_label":           "F2",
    "f2_exe":             "",
    "f3_label":           "F3",
    "f3_exe":             "",
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
        try:
            with open(self._prefs_path(), "r", encoding="utf-8") as f:
                saved = json.load(f)
            for k in self.prefs:
                if k in saved and k not in PREFS_SKIP:
                    self.prefs[k] = saved[k]
        except (FileNotFoundError, Exception):
            pass

    def save(self) -> None:
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
            parent=parent, title="Export configuration",
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
            messagebox.showinfo("Exported", f"Configuration saved to:\n{path}", parent=parent)
        except Exception as e:
            messagebox.showerror("Export failed", f"Could not write file:\n{e}", parent=parent)

    def import_config(self, parent: tk.Widget) -> None:
        path = filedialog.askopenfilename(
            parent=parent, title="Import configuration",
            filetypes=[("JSON config", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except Exception as e:
            messagebox.showerror("Import failed", f"Could not read file:\n{e}", parent=parent)
            return
        for k in self.prefs:
            if k in loaded and k not in PREFS_SKIP:
                self.prefs[k] = loaded[k]
        messagebox.showinfo(
            "Imported",
            f"Configuration loaded from:\n{path}\n\nOpen Preferences to review.",
            parent=parent,
        )

    # ── Robocopy helpers ─────────────────────────────────────────────

    def build_robocopy_cmd(self) -> list[str] | None:
        p = self.prefs
        if not p["rcopy_enabled"]:
            return None
        src = p["rcopy_src"].strip() or p["save_dir"]
        dst = p["rcopy_dst"].strip()
        if not src or not dst:
            return None
        ext_list = p.get("rcopy_ext", [])
        if not ext_list:
            return None
        cmd = ["robocopy", src, dst] + ext_list
        if p["rcopy_mir"]:
            cmd.append("/MIR")
        else:
            cmd.append("/E")
        if p["rcopy_z"]:
            cmd.append("/Z")
        cmd += [f"/R:{int(p['rcopy_retries'])}", f"/W:{int(p['rcopy_wait'])}"]
        if p["rcopy_log"] and p["rcopy_log_path"].strip():
            cmd.append(f"/LOG+:{p['rcopy_log_path'].strip()}")
        cmd += ["/NP", "/NDL"]
        return cmd

    def should_run_robocopy(self, src_dir: str) -> bool:
        p = self.prefs
        if not p.get("rcopy_enabled", False):
            return False
        mode = p.get("rcopy_trigger", "every")
        if mode == "off":
            return False
        if mode == "every":
            return True
        n = max(1, int(p.get("rcopy_n_chunks", 5)))
        try:
            count = sum(1 for f in os.listdir(src_dir)
                        if f.lower().endswith(".blf"))
        except Exception:
            return False
        return count > 0 and count % n == 0

    def run_robocopy(self) -> None:
        cmd = self.build_robocopy_cmd()
        if cmd is None:
            return
        try:
            subprocess.Popen(
                cmd,
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════
    #  PREFERENCES DIALOG
    #
    #  Architecture: buttons are created BEFORE any tab code runs.
    #  Widget variables are stored in a dict W{}.  The save function
    #  reads from W at click-time; any key not yet populated simply
    #  keeps its current prefs value.  Each tab is wrapped in
    #  try/except so a bug in one tab cannot hide the buttons.
    # ══════════════════════════════════════════════════════════════════

    def open_dialog(self, parent: tk.Widget, on_apply: Callable[[], None]) -> None:
        win = tk.Toplevel(parent)
        win.title("Preferences")
        win.geometry("820x700")
        win.resizable(True, True)
        win.configure(bg=th.DARK_BG)
        win.grab_set()

        # ── Widget variable store ─────────────────────────────────────
        # Populated by each tab builder below.  The save function reads
        # whatever is present at click-time.
        W: dict = {}

        # ── Save function ─────────────────────────────────────────────
        def apply_and_close():
            try:
                # General tab
                if "dir_var" in W:
                    self.prefs["save_dir"] = W["dir_var"].get().strip() or self.prefs["save_dir"]
                if "mode_var" in W:
                    self.prefs["filename_mode"] = W["mode_var"].get()
                if "prefix_var" in W:
                    self.prefs["filename_prefix"] = W["prefix_var"].get().strip() or "CAN_Record"
                if "counter_var" in W:
                    self.prefs["filename_counter"] = W["counter_var"].get()
                if "comment_text" in W:
                    self.prefs["meta_comment"] = W["comment_text"].get("1.0", "end-1c").strip()
                if "chunk_var" in W:
                    self.prefs["chunk_duration"] = CHUNK_OPTIONS.get(W["chunk_var"].get(), 0)
                if "fn_vars" in W:
                    for key, var in W["fn_vars"].items():
                        self.prefs[key] = var.get().strip()
                # Robocopy tab
                if "rcopy_enabled_var" in W:
                    self.prefs["rcopy_enabled"]  = W["rcopy_enabled_var"].get()
                if "rcopy_src_var" in W:
                    self.prefs["rcopy_src"]      = W["rcopy_src_var"].get().strip()
                if "rcopy_dst_var" in W:
                    self.prefs["rcopy_dst"]      = W["rcopy_dst_var"].get().strip()
                if "ext_vars" in W:
                    self.prefs["rcopy_ext"]      = [p for p, v in W["ext_vars"].items() if v.get()]
                if "rcopy_mir_var" in W:
                    self.prefs["rcopy_mir"]      = W["rcopy_mir_var"].get()
                if "rcopy_z_var" in W:
                    self.prefs["rcopy_z"]        = W["rcopy_z_var"].get()
                if "rcopy_retries_var" in W:
                    self.prefs["rcopy_retries"]  = W["rcopy_retries_var"].get()
                if "rcopy_wait_var" in W:
                    self.prefs["rcopy_wait"]     = W["rcopy_wait_var"].get()
                if "rcopy_log_var" in W:
                    self.prefs["rcopy_log"]      = W["rcopy_log_var"].get()
                if "rcopy_log_path_var" in W:
                    self.prefs["rcopy_log_path"] = W["rcopy_log_path_var"].get().strip()
                if "rcopy_trigger_var" in W:
                    self.prefs["rcopy_trigger"]  = W["rcopy_trigger_var"].get()
                if "rcopy_n_var" in W:
                    self.prefs["rcopy_n_chunks"] = W["rcopy_n_var"].get()
                # Session Info tab
                if "si_vars" in W:
                    for key, var in W["si_vars"].items():
                        self.prefs[key] = var.get().strip()
            except Exception:
                pass
            self.save()
            on_apply()
            win.destroy()

        # ══════════════════════════════════════════════════════════════
        #  BUTTONS — created FIRST so they always exist
        # ══════════════════════════════════════════════════════════════
        btn_bar = tk.Frame(win, bg=th.PANEL_BG, pady=8, padx=12)
        btn_bar.pack(side="bottom", fill="x")

        tk.Button(btn_bar, text="  Save and Exit  ",
                  command=apply_and_close,
                  bg=th.ACCENT, fg="#000000",
                  activebackground=th.ACCENT2, activeforeground="#000000",
                  font=("Consolas", 11, "bold"),
                  relief="flat", padx=16, pady=6).pack(side="left", padx=(0, 12))

        tk.Button(btn_bar, text="  Cancel  ",
                  command=win.destroy,
                  bg=th.MUTED, fg="#ffffff",
                  activebackground="#666666", activeforeground="#ffffff",
                  font=("Consolas", 11, "bold"),
                  relief="flat", padx=16, pady=6).pack(side="left")

        # ── Separator above buttons ───────────────────────────────────
        tk.Frame(win, bg=th.MUTED, height=1).pack(side="bottom", fill="x")

        # ══════════════════════════════════════════════════════════════
        #  NOTEBOOK — built after buttons
        # ══════════════════════════════════════════════════════════════
        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        # ════════════════════════════════════════════════════════════
        #  TAB 1 — General
        # ════════════════════════════════════════════════════════════
        gen_tab = ttk.Frame(nb)
        nb.add(gen_tab, text="  General  ")
        try:
            self._build_general_tab(gen_tab, W)
        except Exception as e:
            tk.Label(gen_tab, text=f"Error building General tab:\n{e}",
                     bg=th.DARK_BG, fg="red", font=("Consolas", 10),
                     wraplength=700, justify="left").pack(padx=12, pady=12)

        # ════════════════════════════════════════════════════════════
        #  TAB 2 — Robocopy
        # ════════════════════════════════════════════════════════════
        rc_tab = ttk.Frame(nb)
        nb.add(rc_tab, text="  Robocopy  ")
        try:
            self._build_robocopy_tab(rc_tab, W, win)
        except Exception as e:
            tk.Label(rc_tab, text=f"Error building Robocopy tab:\n{e}",
                     bg=th.DARK_BG, fg="red", font=("Consolas", 10),
                     wraplength=700, justify="left").pack(padx=12, pady=12)

        # ════════════════════════════════════════════════════════════
        #  TAB 3 — Session Info
        # ════════════════════════════════════════════════════════════
        si_tab = ttk.Frame(nb)
        nb.add(si_tab, text="  Session Info  ")
        try:
            self._build_session_info_tab(si_tab, W)
        except Exception as e:
            tk.Label(si_tab, text=f"Error building Session Info tab:\n{e}",
                     bg=th.DARK_BG, fg="red", font=("Consolas", 10),
                     wraplength=700, justify="left").pack(padx=12, pady=12)

    # ── TAB BUILDERS ─────────────────────────────────────────────────

    def _build_general_tab(self, tab: ttk.Frame, W: dict) -> None:
        """Build the General tab and store widget vars in W."""

        # ── Save directory ───────────────────────────────────────────
        dir_frame = ttk.LabelFrame(tab, text="Recording Save Directory", padding=8)
        dir_frame.pack(fill="x", padx=12, pady=(12, 4))

        dir_var = tk.StringVar(value=self.prefs["save_dir"])
        W["dir_var"] = dir_var
        ttk.Entry(dir_frame, textvariable=dir_var, width=52).grid(
            row=0, column=0, padx=(0, 6), pady=4, sticky="we")

        def browse_dir():
            chosen = filedialog.askdirectory(initialdir=dir_var.get())
            if chosen:
                dir_var.set(chosen)

        ttk.Button(dir_frame, text="Browse...", command=browse_dir).grid(
            row=0, column=1, pady=4)
        dir_frame.columnconfigure(0, weight=1)

        # ── File naming ──────────────────────────────────────────────
        name_frame = ttk.LabelFrame(tab, text="File Naming", padding=8)
        name_frame.pack(fill="x", padx=12, pady=4)

        mode_var = tk.StringVar(value=self.prefs["filename_mode"])
        W["mode_var"] = mode_var
        ttk.Radiobutton(name_frame,
                        text="Timestamp  (e.g. CAN_Record_20250601_143012.blf)",
                        variable=mode_var, value="timestamp",
                        ).grid(row=0, column=0, columnspan=5, sticky="w", pady=2)
        ttk.Radiobutton(name_frame, text="Prefix + counter  (e.g.",
                        variable=mode_var, value="prefix_counter",
                        ).grid(row=1, column=0, sticky="w", pady=2)

        prefix_var = tk.StringVar(value=self.prefs["filename_prefix"])
        counter_var = tk.IntVar(value=self.prefs["filename_counter"])
        W["prefix_var"]  = prefix_var
        W["counter_var"] = counter_var
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
                preview_var.set(f"Preview:  {prefix}_{counter_var.get():04d}.blf")

        mode_var.trace_add("write", update_preview)
        prefix_var.trace_add("write", update_preview)
        counter_var.trace_add("write", update_preview)
        update_preview()
        ttk.Label(name_frame, textvariable=preview_var,
                  foreground=th.ACCENT2).grid(
            row=2, column=0, columnspan=5, sticky="w", pady=(6, 2))

        # ── Recording comment ─────────────────────────────────────────
        cmt_frame = ttk.LabelFrame(tab, text="Recording Comment", padding=8)
        cmt_frame.pack(fill="x", padx=12, pady=4)

        comment_text = tk.Text(cmt_frame, height=2, width=50,
                               bg=th.PANEL_BG, fg=th.TEXT_FG,
                               insertbackground=th.TEXT_FG,
                               relief="flat", font=("Consolas", 10))
        comment_text.insert("1.0", self.prefs["meta_comment"])
        comment_text.pack(fill="x", expand=True)
        W["comment_text"] = comment_text

        # ── Chunk recording ──────────────────────────────────────────
        chunk_frame = ttk.LabelFrame(tab, text="Chunk Recording", padding=8)
        chunk_frame.pack(fill="x", padx=12, pady=4)

        ttk.Label(chunk_frame, text="Split recording every:").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        chunk_var = tk.StringVar(
            value=_CHUNK_LABEL.get(self.prefs["chunk_duration"], "Off"))
        W["chunk_var"] = chunk_var
        ttk.Combobox(chunk_frame, textvariable=chunk_var,
                     values=list(CHUNK_OPTIONS.keys()),
                     state="readonly", width=12,
                     ).grid(row=0, column=1, sticky="w", pady=4)

        # ── Function buttons ─────────────────────────────────────────
        fn_frame = ttk.LabelFrame(
            tab, text="Function Buttons  (F1 / F2 / F3)", padding=8)
        fn_frame.pack(fill="x", padx=12, pady=4)

        fn_vars: dict[str, tk.StringVar] = {}
        for n in (1, 2, 3):
            ttk.Label(fn_frame, text=f"F{n} label:").grid(
                row=n - 1, column=0, sticky="w", padx=(0, 6), pady=3)
            lbl_v = tk.StringVar(value=self.prefs[f"f{n}_label"])
            fn_vars[f"f{n}_label"] = lbl_v
            ttk.Entry(fn_frame, textvariable=lbl_v, width=14).grid(
                row=n - 1, column=1, padx=(0, 12), pady=3)
            ttk.Label(fn_frame, text="Exe:").grid(
                row=n - 1, column=2, sticky="w", padx=(0, 6), pady=3)
            exe_v = tk.StringVar(value=self.prefs[f"f{n}_exe"])
            fn_vars[f"f{n}_exe"] = exe_v
            ttk.Entry(fn_frame, textvariable=exe_v, width=38).grid(
                row=n - 1, column=3, sticky="we", pady=3)

            def _browse_exe(var=exe_v):
                p = filedialog.askopenfilename(
                    title="Select executable",
                    filetypes=[("Executables", "*.exe *.bat *.cmd"),
                               ("All files", "*.*")])
                if p:
                    var.set(p)

            ttk.Button(fn_frame, text="...", width=2,
                       command=_browse_exe).grid(
                row=n - 1, column=4, padx=(4, 0), pady=3)
        fn_frame.columnconfigure(3, weight=1)
        W["fn_vars"] = fn_vars

    def _build_robocopy_tab(self, tab: ttk.Frame, W: dict,
                            win: tk.Toplevel) -> None:
        """Build the Robocopy tab and store widget vars in W."""

        # ── Master on/off ────────────────────────────────────────────
        rc_top = ttk.Frame(tab)
        rc_top.pack(fill="x", padx=12, pady=(12, 0))

        rcopy_enabled_var = tk.BooleanVar(value=self.prefs["rcopy_enabled"])
        W["rcopy_enabled_var"] = rcopy_enabled_var
        ttk.Checkbutton(rc_top, text="Enable automatic Robocopy backup",
                        variable=rcopy_enabled_var).pack(side="left")

        # ── Trigger ──────────────────────────────────────────────────
        trigger_frame = ttk.LabelFrame(tab, text="When to Run", padding=8)
        trigger_frame.pack(fill="x", padx=12, pady=(8, 4))

        rcopy_trigger_var = tk.StringVar(
            value=self.prefs.get("rcopy_trigger", "every"))
        W["rcopy_trigger_var"] = rcopy_trigger_var

        ttk.Radiobutton(trigger_frame, text="Off  (manual only)",
                        variable=rcopy_trigger_var, value="off"
                        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=2)
        ttk.Radiobutton(trigger_frame, text="After every chunk",
                        variable=rcopy_trigger_var, value="every"
                        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=2)
        ttk.Radiobutton(trigger_frame, text="After every",
                        variable=rcopy_trigger_var, value="every_n"
                        ).grid(row=2, column=0, sticky="w", pady=2)

        rcopy_n_var = tk.IntVar(value=self.prefs.get("rcopy_n_chunks", 5))
        W["rcopy_n_var"] = rcopy_n_var
        ttk.Spinbox(trigger_frame, textvariable=rcopy_n_var,
                    from_=2, to=999, width=5).grid(
            row=2, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(trigger_frame,
                  text="chunks in source folder").grid(
            row=2, column=2, sticky="w", pady=2)

        # ── Folders ──────────────────────────────────────────────────
        folders_frame = ttk.LabelFrame(tab, text="Folders", padding=8)
        folders_frame.pack(fill="x", padx=12, pady=4)

        ttk.Label(folders_frame, text="Source:").grid(
            row=0, column=0, sticky="w", padx=(0, 6), pady=4)
        rcopy_src_var = tk.StringVar(
            value=self.prefs["rcopy_src"] or self.prefs["save_dir"])
        W["rcopy_src_var"] = rcopy_src_var
        ttk.Entry(folders_frame, textvariable=rcopy_src_var, width=46).grid(
            row=0, column=1, padx=(0, 6), pady=4, sticky="we")

        def browse_src():
            chosen = filedialog.askdirectory(initialdir=rcopy_src_var.get())
            if chosen:
                rcopy_src_var.set(chosen)

        ttk.Button(folders_frame, text="Browse...",
                   command=browse_src).grid(row=0, column=2, pady=4)

        ttk.Label(folders_frame, text="Destination:").grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=4)
        rcopy_dst_var = tk.StringVar(value=self.prefs["rcopy_dst"])
        W["rcopy_dst_var"] = rcopy_dst_var
        ttk.Entry(folders_frame, textvariable=rcopy_dst_var, width=46).grid(
            row=1, column=1, padx=(0, 6), pady=4, sticky="we")

        def browse_dst():
            chosen = filedialog.askdirectory(initialdir=rcopy_dst_var.get() or "/")
            if chosen:
                rcopy_dst_var.set(chosen)

        ttk.Button(folders_frame, text="Browse...",
                   command=browse_dst).grid(row=1, column=2, pady=4)
        folders_frame.columnconfigure(1, weight=1)

        # ── File types ───────────────────────────────────────────────
        ext_frame = ttk.LabelFrame(tab, text="File Types to Copy", padding=8)
        ext_frame.pack(fill="x", padx=12, pady=4)

        saved_exts = set(self.prefs.get("rcopy_ext", []))
        ext_vars: dict[str, tk.BooleanVar] = {}
        for col_i, (pattern, label) in enumerate(RCOPY_EXT_OPTIONS):
            var = tk.BooleanVar(value=(pattern in saved_exts))
            ext_vars[pattern] = var
            ttk.Checkbutton(ext_frame, text=f"{pattern}  {label}",
                            variable=var).grid(
                row=col_i // 4, column=col_i % 4,
                sticky="w", padx=(0, 16), pady=2)
        W["ext_vars"] = ext_vars

        # ── Copy options ─────────────────────────────────────────────
        opts_frame = ttk.LabelFrame(tab, text="Copy Options", padding=8)
        opts_frame.pack(fill="x", padx=12, pady=4)

        rcopy_mir_var = tk.BooleanVar(value=self.prefs["rcopy_mir"])
        W["rcopy_mir_var"] = rcopy_mir_var
        ttk.Checkbutton(opts_frame, text="/MIR  Mirror (deletes extras at destination)",
                        variable=rcopy_mir_var).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=2)

        rcopy_z_var = tk.BooleanVar(value=self.prefs["rcopy_z"])
        W["rcopy_z_var"] = rcopy_z_var
        ttk.Checkbutton(opts_frame, text="/Z  Resumable mode",
                        variable=rcopy_z_var).grid(
            row=1, column=0, columnspan=4, sticky="w", pady=2)

        ttk.Label(opts_frame, text="Retries /R:").grid(
            row=2, column=0, sticky="w", padx=(0, 4), pady=4)
        rcopy_retries_var = tk.IntVar(value=self.prefs["rcopy_retries"])
        W["rcopy_retries_var"] = rcopy_retries_var
        ttk.Spinbox(opts_frame, textvariable=rcopy_retries_var,
                    from_=0, to=99, width=5).grid(row=2, column=1, sticky="w", pady=4)

        ttk.Label(opts_frame, text="Wait /W:").grid(
            row=2, column=2, sticky="w", padx=(16, 4), pady=4)
        rcopy_wait_var = tk.IntVar(value=self.prefs["rcopy_wait"])
        W["rcopy_wait_var"] = rcopy_wait_var
        ttk.Spinbox(opts_frame, textvariable=rcopy_wait_var,
                    from_=0, to=300, width=5).grid(row=2, column=3, sticky="w", pady=4)

        # ── Log file ─────────────────────────────────────────────────
        log_frame = ttk.LabelFrame(tab, text="Log File (optional)", padding=8)
        log_frame.pack(fill="x", padx=12, pady=4)

        rcopy_log_var = tk.BooleanVar(value=self.prefs["rcopy_log"])
        W["rcopy_log_var"] = rcopy_log_var
        ttk.Checkbutton(log_frame, text="Write log (/LOG+:)",
                        variable=rcopy_log_var).grid(row=0, column=0, sticky="w", pady=2)

        rcopy_log_path_var = tk.StringVar(value=self.prefs["rcopy_log_path"])
        W["rcopy_log_path_var"] = rcopy_log_path_var
        ttk.Entry(log_frame, textvariable=rcopy_log_path_var, width=40).grid(
            row=0, column=1, padx=(8, 6), pady=2, sticky="we")

        def browse_log():
            path = filedialog.asksaveasfilename(
                title="Robocopy log file", defaultextension=".log",
                filetypes=[("Log files", "*.log"), ("All files", "*.*")],
                initialfile="robocopy.log")
            if path:
                rcopy_log_path_var.set(path)

        ttk.Button(log_frame, text="Browse...", command=browse_log).grid(
            row=0, column=2, pady=2)
        log_frame.columnconfigure(1, weight=1)

        # ── Run now button ────────────────────────────────────────────
        run_frame = ttk.Frame(tab)
        run_frame.pack(fill="x", padx=12, pady=(4, 8))

        def run_now():
            cmd = self.build_robocopy_cmd()
            if not cmd:
                messagebox.showwarning(
                    "Robocopy", "Enable robocopy and set folders first.", parent=win)
                return
            if messagebox.askyesno("Run Robocopy",
                                   f"Run this command now?\n\n{' '.join(cmd)}",
                                   parent=win):
                try:
                    subprocess.Popen(cmd,
                                     creationflags=subprocess.CREATE_NO_WINDOW,
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                    messagebox.showinfo("Robocopy", "Started in background.", parent=win)
                except Exception as e:
                    messagebox.showerror("Failed", f"Could not start robocopy:\n{e}", parent=win)

        ttk.Button(run_frame, text="Run now", command=run_now).pack(side="left")

    def _build_session_info_tab(self, tab: ttk.Frame, W: dict) -> None:
        """Build the Session Info tab and store widget vars in W."""
        si_outer = ttk.LabelFrame(
            tab, text="Session Info Template  (saved alongside every BLF)",
            padding=8)
        si_outer.pack(fill="x", padx=12, pady=(12, 4))

        si_fields = [
            ("Project:",       "meta_project"),
            ("Vehicle:",       "meta_vehicle"),
            ("Driver:",        "meta_driver"),
            ("Configuration:", "meta_config"),
        ]
        si_vars: dict[str, tk.StringVar] = {}
        for row_i, (label, key) in enumerate(si_fields):
            ttk.Label(si_outer, text=label).grid(
                row=row_i, column=0, sticky="w", padx=(0, 6), pady=3)
            v = tk.StringVar(value=self.prefs[key])
            si_vars[key] = v
            ttk.Entry(si_outer, textvariable=v, width=50).grid(
                row=row_i, column=1, sticky="we", pady=3)
        si_outer.columnconfigure(1, weight=1)
        W["si_vars"] = si_vars
