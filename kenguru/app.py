"""
kenguru.app
~~~~~~~~~~~
CANLoggerApp — the main application window.

After refactoring this class is a thin shell that:
  1. Creates one instance of each manager (PreferencesManager, CameraManager,
     CANSession, ExportManager).
  2. Builds the Tkinter widget tree.
  3. Runs the periodic UI-update loop.
  4. Routes toolbar-button clicks to the appropriate manager.

Business logic lives in the manager modules; this file only handles
widget creation, event binding, and the scheduler.
"""
from __future__ import annotations

import os
import subprocess
import tkinter as tk
from collections import deque
from tkinter import messagebox, ttk

try:
    import cv2 as _cv2
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False

try:
    import matplotlib
    matplotlib.use("TkAgg")
except ImportError:
    pass

from . import theme as th
from .camera         import CameraManager
from .can_session    import CANSession
from .export         import ExportManager
from .preferences    import PreferencesManager
from .review_window  import open_review_window
from .signal_selector import open_signal_selector


class CANLoggerApp:
    """Main application window — wires managers together and owns the widget tree."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Kenguru CAN monitor v2.4.8 - Gusztav Gombas")

        # ── Managers ─────────────────────────────────────────────
        self.prefs_mgr  = PreferencesManager()
        self.camera     = CameraManager(self)
        self.session    = CANSession(self)
        self.export_mgr = ExportManager(self)

        # ── UI state ─────────────────────────────────────────────
        self._tree_font_size: int = 12
        self.tree_items:      dict = {}

        # Convenience alias so managers can read prefs via self.prefs_mgr.prefs
        # without an extra attribute lookup
        self._prefs = self.prefs_mgr.prefs   # direct dict reference

        self.create_widgets()
        self._build_menubar()
        self._apply_theme()
        self.session.auto_detect_channels()
        if _CV2_AVAILABLE:
            self.camera.detect_cameras()
        self.set_status("idle")

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # ── Theme ─────────────────────────────────────────────────────────

    def _apply_theme(self) -> None:
        th.apply_theme(
            self.root, self._prefs, self._tree_font_size,
            self.status_label, self.rec_stats_label,
        )

    # ── Menu ─────────────────────────────────────────────────────────

    def _build_menubar(self) -> None:
        mb = tk.Menu(self.root, tearoff=False,
                     bg=th.PANEL_BG, fg=th.TEXT_FG,
                     activebackground=th.ACCENT, activeforeground="#ffffff")
        prefs_menu = tk.Menu(mb, tearoff=False,
                             bg=th.PANEL_BG, fg=th.TEXT_FG,
                             activebackground=th.ACCENT, activeforeground="#ffffff")
        prefs_menu.add_command(label="Preferences…",
                               accelerator="Ctrl+P",
                               command=self.open_preferences)
        prefs_menu.add_separator()
        prefs_menu.add_command(label="Export configuration…",
                               command=lambda: self.prefs_mgr.export_config(self.root))
        prefs_menu.add_command(label="Import configuration…",
                               command=self._import_config)
        mb.add_cascade(label="Preferences (Ctrl+P)", menu=prefs_menu)
        self.root.config(menu=mb)
        self.root.bind("<Control-p>", lambda e: self.open_preferences())

    def open_preferences(self) -> None:
        self.prefs_mgr.open_dialog(
            parent=self.root,
            on_apply=self._on_prefs_applied,
        )

    def _on_prefs_applied(self) -> None:
        self._refresh_fn_buttons()
        self._apply_theme()

    def _import_config(self) -> None:
        self.prefs_mgr.import_config(self.root)
        self._refresh_fn_buttons()

    # ── Function buttons ──────────────────────────────────────────────

    def _launch_fn(self, n: int) -> None:
        exe = self._prefs.get(f"f{n}_exe", "").strip()
        if not exe:
            messagebox.showinfo(
                "Not configured",
                f"F{n} has no application configured.\n"
                "Open Preferences to assign an executable.")
            return
        if not os.path.exists(exe):
            messagebox.showerror("Not found", f"Could not find the executable:\n{exe}")
            return
        try:
            subprocess.Popen([exe])
        except Exception as e:
            messagebox.showerror("Launch failed", f"Could not launch:\n{exe}\n\n{e}")

    def _refresh_fn_buttons(self) -> None:
        for n, btn in ((1, self.f1_btn), (2, self.f2_btn), (3, self.f3_btn)):
            label = self._prefs.get(f"f{n}_label", "").strip() or f"F{n}"
            btn.config(text=label)

    # ── Connection helpers ────────────────────────────────────────────

    def connect(self, silent: bool = False) -> None:
        self.session.connect(silent=silent)

    def on_interface_changed(self, event=None) -> None:
        self.session.auto_detect_channels()

    def _on_fd_toggled(self) -> None:
        if self.fd_var.get():
            self.fd_data_br_label.grid()
            self.fd_data_br_entry.grid()
        else:
            self.fd_data_br_label.grid_remove()
            self.fd_data_br_entry.grid_remove()

    def change_update_rate(self, event=None) -> None:
        self.session.update_rate_hz = int(self.update_rate_var.get().split()[0])

    # ── Session pass-through ──────────────────────────────────────────

    def start_recording(self)  -> None: self.session.start_recording()
    def start_listen_only(self)-> None: self.session.start_listen_only()
    def stop(self)             -> None: self.session.stop()
    def load_dbc(self)         -> None: self.session.load_dbc()
    def remove_dbc(self)       -> None: self.session.remove_dbc()

    def select_signals(self) -> None:
        open_signal_selector(self)

    def open_review_window(self) -> None:
        open_review_window(self)

    def save_blf(self)     -> None: self.export_mgr.save_blf()
    def export_csv(self)   -> None: self.export_mgr.export_csv()
    def export_mf4(self)   -> None: self.export_mgr.export_mf4()

    # ── Tree management ───────────────────────────────────────────────

    def initialize_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.tree_items.clear()
        with self.session._lock:
            self.session.signal_latest_values.clear()
            self.session.signal_last_seen.clear()
        for full_name, info in self.session.selected_signals.items():
            item = self.tree.insert("", "end",
                                    values=(full_name, "-", info["unit"], "-"))
            self.tree_items[full_name] = item

    # ── UI update loop ────────────────────────────────────────────────

    @property
    def update_rate_hz(self) -> int:
        return getattr(self.session, "_update_rate_hz", 10)

    def _schedule_ui_update(self) -> None:
        if not self.session._running.is_set():
            return
        self._do_ui_update()
        self.root.after(
            int(1000 / self.update_rate_hz), self._schedule_ui_update)

    def _do_ui_update(self) -> None:
        with self.session._lock:
            snapshot        = dict(self.session.signal_latest_values)
            last_seen_snap  = dict(self.session.signal_last_seen)

        for full_name, value in snapshot.items():
            if full_name not in self.tree_items:
                continue
            info     = self.session.selected_signals[full_name]
            decimals = info.get("decimals", 2)
            scale    = info.get("scale",    1.0)
            offset   = info.get("offset",   0.0)
            try:
                converted = float(value) * scale + offset
                formatted = f"{converted:.{decimals}f}"
            except (TypeError, ValueError):
                formatted = str(value)
            last_seen = last_seen_snap.get(full_name, "-")
            self.tree.item(self.tree_items[full_name],
                           values=(full_name, formatted, info["unit"], last_seen))

        self._update_rec_stats()

    def _update_rec_stats(self) -> None:
        """Refresh recording-size / write-speed label in the status bar."""
        blf = self.session.last_blf_filename
        if not (self.session.recording and blf and os.path.exists(blf)):
            self.rec_stats_label.pack_forget()
            return

        import time
        now = time.monotonic()
        try:
            size = os.path.getsize(blf)
        except OSError:
            return

        self.session._rec_size_history.append((now, size))
        while len(self.session._rec_size_history) > 1 and \
              now - self.session._rec_size_history[0][0] > 3.0:
            self.session._rec_size_history.popleft()

        if len(self.session._rec_size_history) >= 2:
            t_old, s_old = self.session._rec_size_history[0]
            t_new, s_new = self.session._rec_size_history[-1]
            dt    = t_new - t_old
            speed = (s_new - s_old) / dt if dt > 0 else 0.0
        else:
            speed = 0.0

        def fmt_bytes(b: float) -> str:
            b = max(0, b)
            if b < 1024:
                return f"{b:.0f} B"
            elif b < 1024 ** 2:
                return f"{b / 1024:.1f} KB"
            else:
                return f"{b / 1024 ** 2:.2f} MB"

        self.rec_stats_label.pack(side="right")
        self.rec_stats_label.config(
            text=f"  {fmt_bytes(size)}  |  {fmt_bytes(speed)}/s  ",
            bg="#8B0000",
        )

    # ── Status bar ───────────────────────────────────────────────────

    def set_status(self, mode: str) -> None:
        if mode == "idle":
            self.status_label.config(text="IDLE", bg="green")
        elif mode == "listen":
            self.status_label.config(text="LISTEN ONLY IN PROGRESS", bg="orange")
        elif mode == "record":
            self.status_label.config(text="RECORDING IN PROGRESS", bg="red")

    def _resize_tree_font(self, delta: int) -> None:
        self._tree_font_size = max(7, min(24, self._tree_font_size + delta))
        fs  = self._tree_font_size
        s   = ttk.Style(self.root)
        s.configure("Treeview",
                    font=("Consolas", fs),
                    rowheight=int(fs * 2.25))
        s.configure("Treeview.Heading", font=("Consolas", fs, "bold"))
        self._tree_font_size_label.config(text=f"{fs} pt")

    # ── Shutdown ─────────────────────────────────────────────────────

    def on_closing(self) -> None:
        if self.session._running.is_set():
            self.session.stop()
        self.prefs_mgr.save()
        self.camera._stop_preview()
        self.camera.release_camera()
        self.root.destroy()

    # ── Widget construction ───────────────────────────────────────────

    def create_widgets(self) -> None:
        main_frame = ttk.Frame(self.root, padding=6)
        main_frame.pack(fill="both", expand=True)

        # ═══════════ CONNECTION & CAMERA FRAME ═══════════════════════
        conn_frame = ttk.LabelFrame(main_frame, text="Connection & Camera", padding=6)
        conn_frame.pack(fill="x", pady=(0, 3))

        # Row 0: interface / channel / bitrate / FD / update rate / connect
        ttk.Label(conn_frame, text="Interface:").grid(
            row=0, column=0, sticky="w", padx=4, pady=2)
        self.interface_var = tk.StringVar(value="Vector")
        interface_menu = ttk.Combobox(
            conn_frame, textvariable=self.interface_var,
            values=["Vector", "Canalyst-II", "Virtual CAN"],
            state="readonly", width=12)
        interface_menu.grid(row=0, column=1, padx=4, pady=2)
        interface_menu.bind("<<ComboboxSelected>>", self.on_interface_changed)

        self.channel_label = ttk.Label(conn_frame, text="Channel:")
        self.channel_label.grid(row=0, column=2, sticky="w", padx=4, pady=2)
        self.channel_var = tk.StringVar()
        self.channel_dropdown = ttk.Combobox(
            conn_frame, textvariable=self.channel_var, state="readonly", width=10)
        self.channel_dropdown.grid(row=0, column=3, padx=4, pady=2)

        ttk.Label(conn_frame, text="Arb. BR (kbps):").grid(
            row=0, column=4, sticky="w", padx=4, pady=2)
        self.bitrate_var = tk.StringVar(value="500")
        ttk.Entry(conn_frame, textvariable=self.bitrate_var, width=7).grid(
            row=0, column=5, padx=4, pady=2)

        self.fd_var   = tk.BooleanVar(value=False)
        self.fd_check = ttk.Checkbutton(
            conn_frame, text="CAN-FD", variable=self.fd_var,
            command=self._on_fd_toggled)
        self.fd_check.grid(row=0, column=6, padx=(8, 2), pady=2, sticky="w")

        self.fd_data_br_label = ttk.Label(conn_frame, text="Data BR (kbps):")
        self.fd_data_br_label.grid(row=0, column=7, sticky="w", padx=4, pady=2)
        self.fd_data_bitrate_var = tk.StringVar(value="2000")
        self.fd_data_br_entry   = ttk.Entry(
            conn_frame, textvariable=self.fd_data_bitrate_var, width=7)
        self.fd_data_br_entry.grid(row=0, column=8, padx=4, pady=2)
        self.fd_data_br_label.grid_remove()
        self.fd_data_br_entry.grid_remove()

        ttk.Label(conn_frame, text="Update Rate:").grid(
            row=0, column=9, sticky="w", padx=4, pady=2)
        self.update_rate_var = tk.StringVar(value="10 Hz")
        rate_menu = ttk.Combobox(
            conn_frame, textvariable=self.update_rate_var,
            values=["1 Hz", "5 Hz", "10 Hz", "25 Hz", "100 Hz"],
            width=7, state="readonly")
        rate_menu.grid(row=0, column=10, padx=4, pady=2)
        rate_menu.bind("<<ComboboxSelected>>", self.change_update_rate)
        # Store on session for the update loop
        self.session._update_rate_hz = 10

        self.connect_btn = ttk.Button(
            conn_frame, text="Connect", command=self.connect)
        self.connect_btn.grid(row=0, column=11, padx=4, pady=2)

        # Row 1: DBC files (multi-load)
        ttk.Label(conn_frame, text="DBC Files:").grid(
            row=1, column=0, sticky="nw", padx=4, pady=2)

        dbc_list_frame = ttk.Frame(conn_frame)
        dbc_list_frame.grid(row=1, column=1, columnspan=7, sticky="we", padx=4, pady=2)

        self.dbc_listbox = tk.Listbox(
            dbc_list_frame, height=3, selectmode="single",
            bg=th.PANEL_BG, fg=th.TEXT_FG, selectbackground=th.SEL_BG,
            font=("Consolas", 9), relief="flat", activestyle="none")
        dbc_sb = ttk.Scrollbar(dbc_list_frame, orient="vertical",
                               command=self.dbc_listbox.yview)
        self.dbc_listbox.configure(yscrollcommand=dbc_sb.set)
        self.dbc_listbox.pack(side="left", fill="both", expand=True)
        dbc_sb.pack(side="right", fill="y")

        dbc_btn_frame = ttk.Frame(conn_frame)
        dbc_btn_frame.grid(row=1, column=8, sticky="ns", padx=4, pady=2)
        ttk.Button(dbc_btn_frame, text="Add DBC",
                   command=self.load_dbc).pack(fill="x", pady=(0, 2))
        ttk.Button(dbc_btn_frame, text="Remove",
                   command=self.remove_dbc).pack(fill="x")

        # Row 2: Camera
        if not _CV2_AVAILABLE:
            ttk.Label(conn_frame,
                      text="Camera: opencv-python not installed  —  pip install opencv-python",
                      foreground="gray").grid(
                row=2, column=0, columnspan=9, sticky="w", padx=4, pady=2)
        else:
            ttk.Label(conn_frame, text="Camera:").grid(
                row=2, column=0, sticky="w", padx=4, pady=2)
            self.cam_var = tk.StringVar(value="None detected")
            self.cam_dropdown = ttk.Combobox(
                conn_frame, textvariable=self.cam_var, state="readonly", width=18)
            self.cam_dropdown.grid(row=2, column=1, padx=4, pady=2)
            self.cam_dropdown.bind("<<ComboboxSelected>>",
                                   self.camera.on_camera_selected)

            ttk.Label(conn_frame, text="Resolution:").grid(
                row=2, column=2, sticky="w", padx=4, pady=2)
            self.cam_res_var = tk.StringVar(value="1280x720")
            ttk.Combobox(conn_frame, textvariable=self.cam_res_var,
                         values=["640x480", "1280x720", "1920x1080"],
                         state="readonly", width=11).grid(
                row=2, column=3, padx=4, pady=2)

            ttk.Label(conn_frame, text="FPS:").grid(
                row=2, column=4, sticky="w", padx=4, pady=2)
            self.cam_fps_var = tk.StringVar(value="30")
            ttk.Combobox(conn_frame, textvariable=self.cam_fps_var,
                         values=["15", "25", "30", "60"],
                         state="readonly", width=5).grid(
                row=2, column=5, padx=4, pady=2)

            ttk.Label(conn_frame, text="Quality:").grid(
                row=2, column=6, sticky="w", padx=4, pady=2)
            self.cam_quality_var = tk.StringVar(value="Medium")
            ttk.Combobox(conn_frame, textvariable=self.cam_quality_var,
                         values=["Low (smallest)", "Medium", "High", "Max (largest)"],
                         state="readonly", width=14).grid(
                row=2, column=7, padx=4, pady=2)

            ttk.Button(conn_frame, text="Detect Cameras",
                       command=self.camera.detect_cameras).grid(
                row=2, column=8, padx=4, pady=2)
            ttk.Button(conn_frame, text="Preview",
                       command=self.camera.open_preview).grid(
                row=2, column=9, padx=4, pady=2)

            self.record_video_var = tk.BooleanVar(value=True)
            ttk.Checkbutton(conn_frame, text="Record video",
                            variable=self.record_video_var).grid(
                row=2, column=10, padx=4, pady=2, sticky="w")

            self.cam_status_label = ttk.Label(
                conn_frame, text="No camera selected", foreground="gray")
            self.cam_status_label.grid(row=2, column=11, padx=8, pady=2)

        conn_frame.columnconfigure(1, weight=1)

        # ═══════════ OPERATION FRAME ═════════════════════════════════
        op_frame = ttk.LabelFrame(main_frame, text="Operation", padding=6)
        op_frame.pack(fill="x", pady=(0, 3))

        # Row 0: transport controls
        ttk.Button(op_frame, text="Select Signals",
                   command=self.select_signals).grid(
            row=0, column=0, padx=4, pady=(2, 1), sticky="we")
        ttk.Button(op_frame, text="Start Recording", style="Start.TButton",
                   command=self.start_recording).grid(
            row=0, column=1, padx=4, pady=(2, 1), sticky="we")
        ttk.Button(op_frame, text="Listen Only",
                   command=self.start_listen_only).grid(
            row=0, column=2, padx=4, pady=(2, 1), sticky="we")
        ttk.Button(op_frame, text="Stop", style="Stop.TButton",
                   command=self.stop).grid(
            row=0, column=3, padx=4, pady=(2, 1), sticky="we")

        # Row 1: file / review operations
        ttk.Button(op_frame, text="Review Recording",
                   command=self.open_review_window).grid(
            row=1, column=0, padx=4, pady=(1, 2), sticky="we")
        ttk.Button(op_frame, text="Save to BLF",
                   command=self.save_blf).grid(
            row=1, column=1, padx=4, pady=(1, 2), sticky="we")
        ttk.Button(op_frame, text="Export CSV",
                   command=self.export_csv).grid(
            row=1, column=2, padx=4, pady=(1, 2), sticky="we")
        ttk.Button(op_frame, text="Export MF4",
                   command=self.export_mf4).grid(
            row=1, column=3, padx=(4, 16), pady=(1, 2), sticky="we")

        # F-buttons
        ttk.Separator(op_frame, orient="vertical").grid(
            row=0, column=4, rowspan=2, sticky="ns", padx=8)
        self.f1_btn = ttk.Button(op_frame,
                                 text=self._prefs["f1_label"],
                                 style="FButton.TButton",
                                 command=lambda: self._launch_fn(1))
        self.f1_btn.grid(row=0, column=5, rowspan=2, padx=4, pady=2, sticky="ns")
        self.f2_btn = ttk.Button(op_frame,
                                 text=self._prefs["f2_label"],
                                 style="FButton.TButton",
                                 command=lambda: self._launch_fn(2))
        self.f2_btn.grid(row=0, column=6, rowspan=2, padx=4, pady=2, sticky="ns")
        self.f3_btn = ttk.Button(op_frame,
                                 text=self._prefs["f3_label"],
                                 style="FButton.TButton",
                                 command=lambda: self._launch_fn(3))
        self.f3_btn.grid(row=0, column=7, rowspan=2, padx=4, pady=2, sticky="ns")

        # Inline camera preview canvas (160×90, stable dimensions)
        ttk.Separator(op_frame, orient="vertical").grid(
            row=0, column=8, rowspan=2, sticky="ns", padx=8)
        self.cam_preview_canvas = tk.Canvas(
            op_frame, width=160, height=90,
            bg="#000000", highlightthickness=1,
            highlightbackground=th.MUTED)
        self.cam_preview_canvas.grid(
            row=0, column=9, rowspan=2, padx=(0, 6), pady=2, sticky="ns")
        self.cam_preview_canvas.create_text(
            80, 45, text="No Preview",
            fill=th.MUTED, font=("Consolas", 9), tags="placeholder")

        # ═══════════ LIVE SIGNAL FRAME ═══════════════════════════════
        signal_frame = ttk.LabelFrame(main_frame, text="Live Signals", padding=4)
        signal_frame.pack(fill="both", expand=True, pady=(0, 3))

        # Font-size controls
        font_ctrl_frame = tk.Frame(signal_frame, bg=th.DARK_BG)
        font_ctrl_frame.pack(side="top", anchor="e", pady=(0, 2))
        tk.Label(font_ctrl_frame, text="Font:", bg=th.DARK_BG,
                 fg=th.MUTED, font=("Consolas", 9)).pack(side="left", padx=(0, 3))
        self._tree_font_size_label = tk.Label(
            font_ctrl_frame, text=f"{self._tree_font_size} pt",
            bg=th.DARK_BG, fg=th.TEXT_FG, font=("Consolas", 9), width=5)
        self._tree_font_size_label.pack(side="left")
        tk.Button(font_ctrl_frame, text="▲",
                  command=lambda: self._resize_tree_font(+1),
                  bg=th.PANEL_BG, fg=th.TEXT_FG, relief="flat",
                  activebackground=th.ACCENT, activeforeground="#fff",
                  font=("Consolas", 8), width=2, bd=0).pack(side="left", padx=1)
        tk.Button(font_ctrl_frame, text="▼",
                  command=lambda: self._resize_tree_font(-1),
                  bg=th.PANEL_BG, fg=th.TEXT_FG, relief="flat",
                  activebackground=th.ACCENT, activeforeground="#fff",
                  font=("Consolas", 8), width=2, bd=0).pack(side="left", padx=1)

        # Signal treeview
        self.tree = ttk.Treeview(
            signal_frame,
            columns=("Signal", "Value", "Unit", "Last Seen"),
            show="headings")
        self.tree.heading("Signal",    text="Signal")
        self.tree.heading("Value",     text="Value")
        self.tree.heading("Unit",      text="Unit")
        self.tree.heading("Last Seen", text="Last Seen")
        self.tree.column("Signal",    width=300, anchor="w")
        self.tree.column("Value",     width=120, anchor="center")
        self.tree.column("Unit",      width=80,  anchor="center")
        self.tree.column("Last Seen", width=120, anchor="center")

        scrollbar = ttk.Scrollbar(signal_frame, orient="vertical",
                                  command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # ═══════════ STATUS BAR ═══════════════════════════════════════
        status_frame = tk.Frame(self.root, bg=th.DARK_BG)
        status_frame.pack(fill="x", side="bottom")

        self.status_label = tk.Label(
            status_frame, text="", fg="white", height=2,
            font=("Consolas", 10, "bold"))
        self.status_label.pack(side="left", fill="x", expand=True)

        self.rec_stats_label = tk.Label(
            status_frame, text="", fg="white",
            bg=th.DARK_BG, height=2, font=("Consolas", 11))
        self.rec_stats_label.pack(side="right")
