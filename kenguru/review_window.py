"""
kenguru.review_window
~~~~~~~~~~~~~~~~~~~~~
Post-recording review window: signal plot, sync-corrected video pane,
and playback controls.

Call ``open_review_window(app)`` to launch the Toplevel.

Why a separate module?
  ``open_review_window`` was previously a single 600-line method on
  ``CANLoggerApp``.  Extracting it means the plot, playback, and video
  logic can be read, tested, and modified independently.  All public
  state needed from the application is passed explicitly, so this module
  has no import-time coupling with ``app.py``.
"""
from __future__ import annotations

import bisect
import os
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import can

from . import theme as th


def open_review_window(app) -> None:
    """Open the review Toplevel for *app*."""
    session = app.session

    # ── BLF selection ────────────────────────────────────────────────
    blf_path = session.last_blf_filename
    if not blf_path or not os.path.exists(blf_path):
        blf_path = filedialog.askopenfilename(
            title="Open BLF for review",
            filetypes=[("BLF files", "*.blf"), ("All files", "*.*")],
        )
        if not blf_path:
            return

    if not session.dbs:
        messagebox.showwarning(
            "Warning", "Load a DBC file first — it is needed to decode signals.")
        return

    # ── Load sync data ────────────────────────────────────────────────
    pts_path        = os.path.splitext(blf_path)[0] + ".pts"
    sync_path       = os.path.splitext(blf_path)[0] + ".sync"
    frame_rel_times: list[float] | None = None
    video_offset: float = 0.0

    # ── Decode BLF ───────────────────────────────────────────────────
    series: dict[str, tuple[list, list]] = {}
    blf_t0 = None
    try:
        with can.BLFReader(blf_path) as reader:
            blf_t0 = reader.start_timestamp
            for msg in reader:
                rel_t = msg.timestamp - blf_t0
                try:
                    db_msg, decoded = session._db_decode(msg.arbitration_id, msg.data)
                except Exception:
                    continue
                for sig_name, value in decoded.items():
                    key = f"{db_msg.name}.{sig_name}"
                    if key not in series:
                        series[key] = ([], [])
                    series[key][0].append(rel_t)
                    series[key][1].append(float(value))
    except Exception as e:
        messagebox.showerror("Error", f"Failed to read BLF:\n{e}")
        return

    if not series:
        messagebox.showwarning("Warning", "No decodable signals found in the BLF.")
        return

    # ── Resolve sync ──────────────────────────────────────────────────
    if os.path.exists(pts_path) and blf_t0 is not None:
        try:
            with open(pts_path) as f:
                lines = [l.strip() for l in f if l.strip()]
            can_elapsed     = float(lines[0])
            frame_rel_times = [float(l) - can_elapsed for l in lines[1:]]
        except Exception:
            frame_rel_times = None

    if frame_rel_times is None and os.path.exists(sync_path):
        try:
            with open(sync_path) as f:
                video_offset = float(f.read().strip())
        except Exception:
            pass

    # ── Find companion video ──────────────────────────────────────────
    try:
        import cv2
        _cv2_ok = True
    except ImportError:
        _cv2_ok = False

    _stem     = os.path.splitext(blf_path)[0]
    video_path = next(
        (p for p in (_stem + ".avi", _stem + ".mp4", _stem + "_raw.avi")
         if _cv2_ok and os.path.exists(p)), None)
    has_video = video_path is not None
    duration  = max(max(ts) for ts, _ in series.values())

    # ── Window ────────────────────────────────────────────────────────
    win = tk.Toplevel(app.root)
    win.title(f"Review — {os.path.basename(blf_path)}")
    win.configure(bg=th.DARK_BG)
    win.geometry("1280x780")

    # ── Formatting helpers ────────────────────────────────────────────
    def _fmt_tc(sec: float) -> str:
        sec = max(0.0, sec)
        return f"{int(sec // 60):02d}:{sec % 60:06.3f}"

    def _parse_tc(s: str) -> float | None:
        s = s.strip()
        try:
            if ":" in s:
                m, rest = s.split(":", 1)
                return int(m) * 60 + float(rest)
            return float(s)
        except ValueError:
            return None

    # ── Playback state ────────────────────────────────────────────────
    pb = {"active": False, "start_wall": 0.0, "start_tc": 0.0}

    # ── Top toolbar: signal selector + TC jump ────────────────────────
    toolbar = tk.Frame(win, bg=th.PANEL_BG, pady=4, padx=8)
    toolbar.pack(fill="x")

    tk.Label(toolbar, text="Signal:", bg=th.PANEL_BG, fg=th.TEXT_FG).pack(side="left")
    sig_var = tk.StringVar(value=sorted(series.keys())[0])
    ttk.Combobox(toolbar, textvariable=sig_var,
                 values=sorted(series.keys()), state="readonly", width=36,
                 ).pack(side="left", padx=(4, 20))

    tk.Label(toolbar, text="Jump to TC:", bg=th.PANEL_BG, fg=th.TEXT_FG).pack(side="left")
    tc_entry_var = tk.StringVar(value="0.000")
    tc_entry     = ttk.Entry(toolbar, textvariable=tc_entry_var, width=10)
    tc_entry.pack(side="left", padx=4)
    tk.Label(toolbar, text="(s or MM:SS.mmm)", bg=th.PANEL_BG,
             fg=th.MUTED, font=("Consolas", 9)).pack(side="left", padx=(0, 8))

    if has_video:
        sync_info = (f"   sync: per-frame pts  ({len(frame_rel_times)} frames)"
                     if frame_rel_times is not None
                     else f"   sync: legacy offset {video_offset:+.3f} s")
    else:
        sync_info = ""
    tk.Label(toolbar,
             text=f"Duration: {_fmt_tc(duration)}{sync_info}",
             bg=th.PANEL_BG, fg=th.MUTED, font=("Consolas", 9),
             ).pack(side="right", padx=8)

    # ── Playback bar ──────────────────────────────────────────────────
    pb_bar = tk.Frame(win, bg=th.PANEL_BG, pady=4, padx=8)
    pb_bar.pack(fill="x")

    play_btn = tk.Button(pb_bar, text="▶  Play",
                         bg=th.ACCENT, fg="#ffffff",
                         activebackground=th.ACCENT2, activeforeground="#ffffff",
                         font=("Consolas", 10, "bold"),
                         relief="flat", padx=12, pady=4)
    play_btn.pack(side="left", padx=(0, 10))

    slider_var = tk.DoubleVar(value=0.0)
    slider     = ttk.Scale(pb_bar, from_=0.0, to=duration,
                            variable=slider_var, orient="horizontal")
    slider.pack(side="left", fill="x", expand=True, padx=(0, 10))

    tc_readout = tk.Label(pb_bar, text="  00:00.000", bg=th.PANEL_BG, fg=th.ACCENT2,
                          font=("Consolas", 11, "bold"), width=14)
    tc_readout.pack(side="left")

    # ── Reference line controls ───────────────────────────────────────
    tk.Frame(pb_bar, bg=th.MUTED, width=1).pack(side="left", fill="y", padx=(12, 8))
    _ref_enabled_var = tk.BooleanVar(value=False)
    tk.Checkbutton(pb_bar, text="Ref line",
                   variable=_ref_enabled_var,
                   bg=th.PANEL_BG, fg=th.TEXT_FG, selectcolor=th.DARK_BG,
                   activebackground=th.PANEL_BG, activeforeground=th.TEXT_FG,
                   font=("Consolas", 10),
                   command=lambda: _toggle_ref()).pack(side="left")
    tk.Label(pb_bar, text=" Y =", bg=th.PANEL_BG, fg=th.MUTED,
             font=("Consolas", 10)).pack(side="left")
    _ref_value_var = tk.StringVar(value="0")
    _ref_entry     = tk.Entry(pb_bar, textvariable=_ref_value_var,
                               bg=th.DARK_BG, fg=th.TEXT_FG,
                               insertbackground=th.TEXT_FG,
                               relief="flat", bd=3, font=("Consolas", 10), width=10)
    _ref_entry.pack(side="left", padx=(4, 0))

    # ── Paned area ────────────────────────────────────────────────────
    pane = tk.PanedWindow(win, orient="vertical",
                          bg=th.DARK_BG, sashwidth=6, sashrelief="flat")
    pane.pack(fill="both", expand=True)

    # ── Plot ──────────────────────────────────────────────────────────
    plot_frame = tk.Frame(pane, bg=th.DARK_BG)
    pane.add(plot_frame, minsize=200)

    import matplotlib
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure
    from matplotlib.ticker import FuncFormatter

    fig = Figure(figsize=(12, 4), dpi=96, facecolor=th.DARK_BG)
    ax  = fig.add_subplot(111, facecolor=th.PANEL_BG)
    ax.tick_params(colors=th.MUTED)
    for sp in ax.spines.values():
        sp.set_edgecolor(th.MUTED)
    ax.set_xlabel("Time (s)", color=th.TEXT_FG, fontsize=9)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.3f}"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.3f}"))

    plot_line,  = ax.plot([], [], color=th.ACCENT, linewidth=1.2)
    cursor_line = ax.axvline(x=0, color="#f38ba8", linewidth=1.5, linestyle="--")

    _ref_line = ax.axhline(y=0, color="#ff0000", linewidth=1.4,
                           linestyle="-", alpha=1.0, zorder=9)
    _ref_line.set_visible(False)

    _tooltip = ax.annotate(
        "",
        xy=(0, 0), xytext=(14, 14),
        textcoords="offset points",
        bbox=dict(boxstyle="round,pad=0.45", facecolor="#1e1e2e",
                  edgecolor=th.MUTED, alpha=0.92, linewidth=0.8),
        fontsize=8, color=th.TEXT_FG, fontfamily="monospace", zorder=20)
    _tooltip.set_visible(False)
    fig.tight_layout(pad=1.2)

    mpl_canvas = FigureCanvasTkAgg(fig, master=plot_frame)
    tb_frame   = tk.Frame(plot_frame, bg=th.PANEL_BG)
    tb_frame.pack(side="bottom", fill="x")
    nav = NavigationToolbar2Tk(mpl_canvas, tb_frame)
    nav.config(bg=th.PANEL_BG)
    for child in nav.winfo_children():
        try:
            child.config(bg=th.PANEL_BG, fg=th.TEXT_FG)
        except Exception:
            pass
    nav.update()
    mpl_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _redraw_signal(*_) -> None:
        key = sig_var.get()
        if key not in series:
            return
        ts, vals = series[key]
        plot_line.set_xdata(ts)
        plot_line.set_ydata(vals)
        ax.set_ylabel(key.split(".")[-1], color=th.TEXT_FG, fontsize=9)
        ax.relim()
        ax.autoscale_view()
        mpl_canvas.draw_idle()

    sig_var.trace_add("write", _redraw_signal)
    _redraw_signal()

    # ── Video pane ────────────────────────────────────────────────────
    _video_cap = None
    vid_fps    = 25.0
    vid_frames = 0

    if has_video:
        vid_frame = tk.Frame(pane, bg=th.DARK_BG)
        pane.add(vid_frame, minsize=160)

        vid_hdr = tk.Frame(vid_frame, bg=th.PANEL_BG, pady=3, padx=8)
        vid_hdr.pack(fill="x")
        import cv2
        _video_cap = cv2.VideoCapture(video_path)
        vid_fps    = _video_cap.get(cv2.CAP_PROP_FPS) or 25.0
        vid_frames = int(_video_cap.get(cv2.CAP_PROP_FRAME_COUNT))
        vid_dur_s  = vid_frames / vid_fps
        _sync_label = (f"per-frame pts ({len(frame_rel_times)} frames)"
                       if frame_rel_times is not None
                       else f"legacy offset {video_offset:+.3f} s")
        tk.Label(vid_hdr,
                 text=(f"Video: {os.path.basename(video_path)}  "
                       f"{_fmt_tc(vid_dur_s)} @ {vid_fps:.1f} fps  |  "
                       f"sync: {_sync_label}"),
                 bg=th.PANEL_BG, fg=th.ACCENT2, font=("Consolas", 9)).pack(side="left")

        vid_label = tk.Label(vid_frame, bg="#000000")
        vid_label.pack(fill="both", expand=True)

        def _show_video_frame() -> None:
            if not _video_cap or not _video_cap.isOpened():
                return
            ret, frame = _video_cap.read()
            if not ret:
                return
            lw = max(vid_label.winfo_width(),  320)
            lh = max(vid_label.winfo_height(), 180)
            fh, fw  = frame.shape[:2]
            scale   = min(lw / fw, lh / fh)
            frame_s = cv2.resize(frame, (max(1, int(fw * scale)),
                                         max(1, int(fh * scale))))
            frame_r = cv2.cvtColor(frame_s, cv2.COLOR_BGR2RGB)
            try:
                from PIL import Image, ImageTk
                img = ImageTk.PhotoImage(Image.fromarray(frame_r))
                vid_label.config(image=img)
                vid_label.image = img
            except ImportError:
                vid_label.config(
                    text="Install Pillow for video:\npip install Pillow", fg="gray")
    else:
        def _show_video_frame() -> None:
            pass

    # ── Seek helpers ─────────────────────────────────────────────────

    def _frame_for_tc(tc: float) -> int:
        """Return the video frame index closest to CAN time *tc*."""
        if frame_rel_times:
            idx = bisect.bisect_left(frame_rel_times, tc)
            if idx >= len(frame_rel_times):
                idx = len(frame_rel_times) - 1
            if idx > 0 and abs(frame_rel_times[idx - 1] - tc) < abs(frame_rel_times[idx] - tc):
                idx -= 1
            return idx
        else:
            video_tc     = max(0.0, tc - video_offset)
            target_frame = int(video_tc * vid_fps)
            return max(0, min(target_frame, vid_frames - 1))

    def _seek_to(tc: float, update_slider: bool = True) -> None:
        tc = max(0.0, min(tc, duration))
        if update_slider:
            slider_var.set(tc)
        tc_readout.config(text=f"  {_fmt_tc(tc)}")
        cursor_line.set_xdata([tc, tc])
        mpl_canvas.draw_idle()
        if has_video and _video_cap is not None:
            _video_cap.set(cv2.CAP_PROP_POS_FRAMES, _frame_for_tc(tc))
            _show_video_frame()

    # ── Playback tick loop ────────────────────────────────────────────

    def _playback_tick() -> None:
        if not pb["active"] or not win.winfo_exists():
            return
        elapsed = time.monotonic() - pb["start_wall"]
        tc = pb["start_tc"] + elapsed
        if tc >= duration:
            tc = duration
            pb["active"] = False
            play_btn.config(text="▶  Play")

        tc_c = max(0.0, min(tc, duration))
        slider_var.set(tc_c)
        tc_readout.config(text=f"  {_fmt_tc(tc_c)}")
        cursor_line.set_xdata([tc_c, tc_c])
        mpl_canvas.draw_idle()

        if has_video and _video_cap is not None and _video_cap.isOpened():
            target_frame = _frame_for_tc(tc_c)
            cur_frame    = int(_video_cap.get(cv2.CAP_PROP_POS_FRAMES))
            if abs(cur_frame - target_frame) <= 1:
                _show_video_frame()
            else:
                _video_cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                _show_video_frame()

        if pb["active"]:
            win.after(40, _playback_tick)

    def _play_pause() -> None:
        if pb["active"]:
            pb["active"] = False
            play_btn.config(text="▶  Play")
        else:
            start_tc = slider_var.get()
            if start_tc >= duration:
                start_tc = 0.0
                _seek_to(0.0)
            pb["active"]     = True
            pb["start_wall"] = time.monotonic()
            pb["start_tc"]   = start_tc
            play_btn.config(text="⏸  Pause")
            _playback_tick()

    play_btn.config(command=_play_pause)

    def _on_slider_moved(event) -> None:
        pb["active"] = False
        play_btn.config(text="▶  Play")
        _seek_to(slider_var.get(), update_slider=False)

    def _on_slider_released(event) -> None:
        pb["start_tc"]   = slider_var.get()
        pb["start_wall"] = time.monotonic()

    slider.bind("<B1-Motion>",       _on_slider_moved)
    slider.bind("<ButtonRelease-1>", _on_slider_released)

    def _jump_to_tc(*_) -> None:
        pb["active"] = False
        play_btn.config(text="▶  Play")
        tc = _parse_tc(tc_entry_var.get())
        if tc is None:
            messagebox.showwarning(
                "Invalid TC",
                "Enter seconds (e.g. 33.321) or MM:SS.mmm (e.g. 00:33.321)")
            return
        _seek_to(tc)

    go_btn = ttk.Button(toolbar, text="Go", command=_jump_to_tc)
    go_btn.pack(side="left")
    tc_entry.bind("<Return>", _jump_to_tc)

    def _on_plot_click(event) -> None:
        if event.inaxes == ax and event.xdata is not None:
            pb["active"] = False
            play_btn.config(text="▶  Play")
            _seek_to(event.xdata)

    mpl_canvas.mpl_connect("button_press_event", _on_plot_click)

    # ── Reference line handlers ───────────────────────────────────────

    def _update_ref() -> None:
        try:
            v = float(_ref_value_var.get())
        except ValueError:
            return
        _ref_line.set_ydata([v, v])
        _ref_line.set_visible(_ref_enabled_var.get())
        mpl_canvas.draw_idle()

    def _toggle_ref() -> None:
        if _ref_enabled_var.get():
            _update_ref()
        else:
            _ref_line.set_visible(False)
            mpl_canvas.draw_idle()

    _ref_entry.bind("<Return>",   lambda e: _update_ref())
    _ref_entry.bind("<KP_Enter>", lambda e: _update_ref())

    # ── Tooltip mouse handlers ────────────────────────────────────────

    def _on_mouse_move(event) -> None:
        if event.inaxes is not ax or event.xdata is None or event.ydata is None:
            _tooltip.set_visible(False)
            mpl_canvas.draw_idle()
            return
        x, y = event.xdata, event.ydata
        _tooltip.set_text(f"x = {x:.4f}\ny = {y:.4f}")
        _tooltip.xy = (x, y)
        ax_x0, ax_x1 = ax.get_xlim()
        ax_y0, ax_y1 = ax.get_ylim()
        x_frac = (x - ax_x0) / (ax_x1 - ax_x0) if ax_x1 != ax_x0 else 0.5
        y_frac = (y - ax_y0) / (ax_y1 - ax_y0) if ax_y1 != ax_y0 else 0.5
        ox = -90 if x_frac > 0.75 else 14
        oy = -42 if y_frac > 0.80 else 14
        _tooltip.set_position((ox, oy))
        _tooltip.set_visible(True)
        mpl_canvas.draw_idle()

    def _on_axes_leave(event) -> None:
        _tooltip.set_visible(False)
        mpl_canvas.draw_idle()

    mpl_canvas.mpl_connect("motion_notify_event", _on_mouse_move)
    mpl_canvas.mpl_connect("axes_leave_event",    _on_axes_leave)

    # ── Window close ──────────────────────────────────────────────────

    def _on_close() -> None:
        pb["active"] = False
        if _video_cap:
            _video_cap.release()
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", _on_close)
    win.after(150, lambda: _seek_to(0.0))
