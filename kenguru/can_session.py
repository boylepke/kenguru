"""
kenguru.can_session
~~~~~~~~~~~~~~~~~~~
CANSession: CAN bus connection, DBC loading, recording lifecycle, and the
background receive loop.

Chunk recording
~~~~~~~~~~~~~~~
When ``prefs["chunk_duration"]`` is non-zero, the receive loop calls
``_rotate_chunk()`` every N seconds.  This closes the current BLF writer,
writes the .pts / .sync / sidecar files for the completed chunk, opens a
fresh BLF, and signals the camera to start a new video file — all from the
receive thread using only thread-safe file I/O and the camera's own lock.
No Tkinter calls are made during rotation.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from datetime import datetime
from tkinter import filedialog, messagebox

import can
import cantools

from .virtual_can import VirtualCANBus


class CANSession:
    """Owns the CAN bus, DBC databases, recording state, and receive loop."""

    def __init__(self, app) -> None:
        self._app = app

        # ── CAN bus ───────────────────────────────────────────────
        self.bus:  can.BusABC | VirtualCANBus | None = None
        self.db:   object | None = None
        self.dbs:  list[tuple[str, object]] = []

        # ── Signal display ────────────────────────────────────────
        self.selected_signals:     dict = {}
        self.signal_latest_values: dict = {}
        self.signal_last_seen:     dict = {}
        self._frame_id_lookup:     dict = {}

        # ── Recording state ───────────────────────────────────────
        self._running:          threading.Event       = threading.Event()
        self.recording:         bool                  = False
        self.blf_writer:        can.BLFWriter | None  = None
        self.last_blf_filename: str | None            = None

        # ── Threading primitives ──────────────────────────────────
        self._lock:       threading.Lock  = threading.Lock()
        self._stop_event: threading.Event = threading.Event()
        self._record_go:  threading.Event = threading.Event()

        # ── Timing references (reset on each chunk) ───────────────
        self._blf_start_time:          float       = 0.0
        self._first_can_msg_time:      float | None = None
        self._first_can_msg_wall_time: float | None = None
        self._record_start_mono:       float        = 0.0
        self._first_can_msg_mono:      float | None = None

        # ── Chunk tracking ────────────────────────────────────────
        self._chunk_start_mono: float = 0.0   # monotonic time when current chunk started

        # ── Recording size tracking ───────────────────────────────
        self._rec_size_history: deque = deque()
        self._update_rate_hz:   int   = 10

    # ── DBC management ───────────────────────────────────────────────

    def load_dbc(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Add DBC file(s)",
            filetypes=[("DBC files", "*.dbc"), ("All files", "*.*")],
        )
        loaded, skipped = [], []
        for path in paths:
            if any(p == path for p, _ in self.dbs):
                skipped.append(os.path.basename(path))
                continue
            non_strict = False
            try:
                db = cantools.database.load_file(path)
            except cantools.db.errors.Error as e:
                ans = messagebox.askyesno(
                    "DBC Warning",
                    f"{os.path.basename(path)} has definition issues:\n\n{e}\n\n"
                    "Load anyway in non-strict mode?\n"
                    "(Overlapping signals will all be decoded independently;\n"
                    "values may be incorrect for the conflicting signals.)",
                )
                if not ans:
                    continue
                try:
                    db = cantools.database.load_file(path, strict=False)
                    non_strict = True
                except Exception as e2:
                    messagebox.showerror(
                        "Error",
                        f"Failed to load DBC even in non-strict mode:\n{path}\n\n{e2}",
                    )
                    continue
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load DBC:\n{path}\n\n{e}")
                continue

            self.dbs.append((path, db))
            label = os.path.basename(path) + ("  non-strict" if non_strict else "")
            dbc_listbox = getattr(self._app, "dbc_listbox", None)
            if dbc_listbox:
                dbc_listbox.insert("end", label)
            loaded.append(label)

        self.db = self.dbs[0][1] if self.dbs else None
        if loaded:
            msg = f"Loaded: {', '.join(loaded)}"
            if skipped:
                msg += f"\nSkipped (already loaded): {', '.join(skipped)}"
            messagebox.showinfo("DBC loaded", msg)

    def remove_dbc(self) -> None:
        dbc_listbox = getattr(self._app, "dbc_listbox", None)
        if dbc_listbox is None:
            return
        sel = dbc_listbox.curselection()
        if not sel:
            messagebox.showwarning("Remove DBC", "Select a DBC in the list first.")
            return
        idx = sel[0]
        self.dbs.pop(idx)
        dbc_listbox.delete(idx)
        self.db = self.dbs[0][1] if self.dbs else None

        all_valid = {
            f"{msg.name}.{sig.name}"
            for _, db in self.dbs
            for msg in db.messages
            for sig in msg.signals
        }
        for k in [k for k in list(self.selected_signals) if k not in all_valid]:
            self.selected_signals.pop(k, None)
        self.rebuild_frame_lookup()
        self._app.initialize_tree()

    def _db_decode(self, arb_id: int, data: bytes):
        for _, db in self.dbs:
            try:
                decoded = db.decode_message(arb_id, data)
                db_msg  = db.get_message_by_frame_id(arb_id)
                return db_msg, decoded
            except Exception:
                continue
        raise KeyError(f"0x{arb_id:X}")

    def db_all_messages(self):
        for _, db in self.dbs:
            yield from db.messages

    def rebuild_frame_lookup(self) -> None:
        self._frame_id_lookup.clear()
        for full_name, info in self.selected_signals.items():
            fid = info["frame_id"]
            self._frame_id_lookup.setdefault(fid, []).append(
                (full_name, info["sig_name"]))

    # ── Connection ────────────────────────────────────────────────────

    def connect(self, silent: bool = False) -> None:
        interface = self._app.interface_var.get()
        fd_mode   = self._app.fd_var.get()
        try:
            if interface == "Virtual CAN":
                if not self.dbs:
                    messagebox.showwarning(
                        "Warning",
                        "Load a DBC file first — the virtual bus needs it to generate frames.",
                    )
                    return

                class _CombinedDB:
                    def __init__(self, dbs):
                        self.messages = [m for _, db in dbs for m in db.messages]

                self.bus = VirtualCANBus(
                    _CombinedDB(self.dbs), msg_rate_hz=100.0, fd=fd_mode)
                if not silent:
                    mode_str = "CAN-FD" if fd_mode else "classic CAN"
                    messagebox.showinfo(
                        "Success",
                        f"Virtual CAN bus started ({mode_str}).\n"
                        "Synthetic frames will be generated from the loaded DBC.",
                    )
                return

            bitrate = int(self._app.bitrate_var.get()) * 1000
            channel = int(self._app.channel_var.get())

            if interface == "Vector":
                if fd_mode:
                    try:
                        data_bitrate = int(self._app.fd_data_bitrate_var.get()) * 1000
                    except ValueError:
                        messagebox.showerror(
                            "Error",
                            "Invalid data bitrate — enter a number in kbps (e.g. 2000).",
                        )
                        return
                    self.bus = can.interface.Bus(
                        interface="vector", channel=channel,
                        bitrate=bitrate, data_bitrate=data_bitrate, fd=True,
                    )
                    if not silent:
                        messagebox.showinfo(
                            "Success",
                            f"Connected to Vector in CAN-FD mode.\n"
                            f"Arb: {bitrate//1000} kbps  |  Data: {data_bitrate//1000} kbps",
                        )
                else:
                    self.bus = can.interface.Bus(
                        interface="vector", channel=channel, bitrate=bitrate)
                    if not silent:
                        messagebox.showinfo(
                            "Success",
                            f"Connected to Vector (classic CAN, {bitrate//1000} kbps).",
                        )

            elif interface == "Canalyst-II":
                self.bus = can.interface.Bus(
                    interface="canalystii", channel=channel, bitrate=bitrate)
                if not silent:
                    messagebox.showinfo(
                        "Success", f"Connected to Canalyst-II ({bitrate//1000} kbps).")

        except Exception as e:
            messagebox.showerror("Error", f"Connection failed:\n{e}")

    def auto_detect_channels(self) -> None:
        interface = self._app.interface_var.get()
        try:
            if interface == "Vector":
                configs  = can.detect_available_configs(interfaces=["vector"])
                channels = [str(cfg["channel"]) for cfg in configs] or ["0"]
                self._app.channel_label.config(text="Vector Channel:")
                self._app.channel_dropdown.config(state="readonly")
                self._app.fd_check.state(["!disabled"])
            elif interface == "Canalyst-II":
                channels = ["0", "1"]
                self._app.channel_label.config(text="Canalyst-II Ch:")
                self._app.channel_dropdown.config(state="readonly")
                self._app.fd_var.set(False)
                self._app._on_fd_toggled()
                self._app.fd_check.state(["disabled"])
            else:
                channels = ["--"]
                self._app.channel_label.config(text="Channel:")
                self._app.channel_dropdown.config(state="disabled")
                self._app.fd_check.state(["!disabled"])
        except Exception:
            channels = ["0"]

        self._app.channel_dropdown["values"] = channels
        self._app.channel_var.set(channels[0])

    # ── Filename helpers ──────────────────────────────────────────────

    def _next_filename(self) -> str:
        """Generate the next BLF filename according to the current naming prefs."""
        prefs    = self._app.prefs_mgr.prefs
        base_dir = prefs["save_dir"]
        if prefs["filename_mode"] == "prefix_counter":
            prefix  = prefs["filename_prefix"]
            counter = prefs["filename_counter"]
            stem    = f"{prefix}_{counter:04d}"
            prefs["filename_counter"] = counter + 1
        else:
            stem = f"CAN_Record_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        return os.path.join(base_dir, f"{stem}.blf")

    def _reset_timing(self) -> None:
        """Reset all per-chunk timing references."""
        self._blf_start_time          = time.time()
        self._first_can_msg_time      = None
        self._first_can_msg_wall_time = None
        self._record_start_mono       = time.monotonic()
        self._first_can_msg_mono      = None
        self._chunk_start_mono        = time.monotonic()

    # ── Sync file helpers ─────────────────────────────────────────────

    def _write_sync_files(self, blf_path: str, blf_t0,
                          frame_pts: list, first_frame_time) -> None:
        """Write .pts and .sync sidecar files for a completed chunk."""
        if not blf_path:
            return
        stem = os.path.splitext(blf_path)[0]

        if frame_pts:
            mono_ref = self._first_can_msg_mono \
                       if self._first_can_msg_mono is not None \
                       else (time.monotonic() - self._record_start_mono)
            try:
                with open(stem + ".pts", "w") as f:
                    f.write(f"{mono_ref:.6f}\n")
                    for t in frame_pts:
                        f.write(f"{t:.6f}\n")
            except Exception:
                pass

        if first_frame_time is not None:
            ref         = blf_t0 or self._first_can_msg_time or self._blf_start_time
            true_offset = first_frame_time - ref
            try:
                with open(stem + ".sync", "w") as f:
                    f.write(f"{true_offset:.6f}\n")
            except Exception:
                pass

    # ── Recording lifecycle ───────────────────────────────────────────

    def start_recording(self)   -> None: self._start(recording=True)
    def start_listen_only(self) -> None: self._start(recording=False)

    def _start(self, recording: bool) -> None:
        if self._running.is_set():
            messagebox.showwarning("Warning", "Already running. Stop first.")
            return
        if not self.selected_signals:
            messagebox.showwarning("Warning", "Select signals first.")
            return
        if not self.bus:
            self.connect(silent=True)
            if not self.bus:
                return
        self._do_start(recording)

    def _do_start(self, recording: bool) -> None:
        if recording:
            prefs    = self._app.prefs_mgr.prefs
            base_dir = prefs["save_dir"]
            os.makedirs(base_dir, exist_ok=True)
            filename = self._next_filename()
            self.last_blf_filename = filename
            self._reset_timing()
            try:
                self.blf_writer = can.BLFWriter(filename)
            except Exception as e:
                messagebox.showerror("Error", f"Cannot create BLF file:\n{e}")
                return

        self._running.set()
        self.recording   = recording
        self._stop_event.clear()

        if recording:
            self._record_go.clear()
        else:
            self._record_go.set()

        self._app.set_status("record" if recording else "listen")

        if recording:
            self._app.camera.start_recording(filename)

        threading.Thread(target=self.receive_loop, daemon=True).start()
        self._app._schedule_ui_update()

        if recording:
            self._record_go.set()

    def stop(self) -> None:
        was_recording = self.recording
        self._stop_event.set()
        self._record_go.set()
        self._running.clear()
        self.recording = False

        blf_t0 = None
        if self.blf_writer:
            blf_t0 = getattr(self.blf_writer, "start_timestamp", None)
            self.blf_writer.stop()
            self.blf_writer = None

        if self.bus:
            try:
                self.bus.shutdown()
            except Exception:
                pass
            self.bus = None

        self._app.set_status("idle")
        self._rec_size_history.clear()
        self._app.rec_stats_label.pack_forget()

        vid_file, frame_pts, first_frame_time = self._app.camera.stop_recording()

        if was_recording and self.last_blf_filename:
            self._write_sync_files(
                self.last_blf_filename, blf_t0, frame_pts, first_frame_time)
            if os.path.exists(self.last_blf_filename):
                self._app.export_mgr.write_sidecar_txt(self.last_blf_filename)

    # ── Chunk rotation ────────────────────────────────────────────────

    def _rotate_chunk(self) -> None:
        """Close the current chunk and open the next one.

        Called exclusively from the receive loop thread — uses only
        thread-safe file I/O and camera locks, no Tkinter calls.
        """
        # 1. Finalise current BLF
        blf_t0 = getattr(self.blf_writer, "start_timestamp", None)
        self.blf_writer.stop()

        # 2. Finalise current video chunk and collect sync data
        vid_file, frame_pts, first_frame_time = self._app.camera.stop_recording()

        # 3. Write sync + sidecar for the completed chunk
        self._write_sync_files(
            self.last_blf_filename, blf_t0, frame_pts, first_frame_time)
        if os.path.exists(self.last_blf_filename):
            self._app.export_mgr.write_sidecar_txt(self.last_blf_filename)

        # 4. Generate new filename and reset timing
        new_filename           = self._next_filename()
        self.last_blf_filename = new_filename
        self._reset_timing()

        # 5. Open new BLF writer
        self.blf_writer = can.BLFWriter(new_filename)

        # 6. Start new video chunk
        self._app.camera.start_recording(new_filename)

        # 7. Fire robocopy if the trigger condition is met
        self._maybe_run_robocopy()

    def _maybe_run_robocopy(self) -> None:
        """Fire robocopy if the configured trigger condition is satisfied.

        Reads the source directory from prefs, counts completed .blf files
        on disk, and delegates the trigger decision to
        ``PreferencesManager.should_run_robocopy()``.  Runs in the receive
        loop thread — uses only file I/O, no Tkinter calls.
        """
        pm  = self._app.prefs_mgr
        src = pm.prefs.get("rcopy_src", "").strip() or pm.prefs.get("save_dir", "")
        if pm.should_run_robocopy(src):
            pm.run_robocopy()

    # ── Receive loop ─────────────────────────────────────────────────

    def receive_loop(self) -> None:
        """Background thread: read messages, write BLF, rotate chunks."""

        # Drain stale hardware buffer before recording starts
        if self.recording and self.bus is not None:
            while True:
                try:
                    stale = self.bus.recv(timeout=0)
                except Exception:
                    break
                if stale is None:
                    break

        # Read chunk duration once at loop start (immutable for this session)
        chunk_secs = self._app.prefs_mgr.prefs.get("chunk_duration", 0)

        while not self._stop_event.is_set():
            bus = self.bus
            if bus is None:
                break
            try:
                msg = bus.recv(timeout=1)
            except can.CanError as e:
                if not self._stop_event.is_set():
                    self._app.root.after(
                        0, lambda err=e: messagebox.showerror(
                            "CAN Error", f"Bus error in receive loop:\n{err}"))
                break
            except Exception as e:
                if not self._stop_event.is_set():
                    self._app.root.after(
                        0, lambda err=e: messagebox.showerror(
                            "Error", f"Unexpected error in receive loop:\n{err}"))
                break

            if msg is None:
                continue

            if self.recording and self.blf_writer:
                self._record_go.wait()
                self.blf_writer.on_message_received(msg)

                if self._first_can_msg_time is None:
                    self._first_can_msg_time      = msg.timestamp
                    self._first_can_msg_wall_time = time.time()
                    self._first_can_msg_mono      = \
                        time.monotonic() - self._record_start_mono

                # ── Chunk boundary check ──────────────────────────
                if chunk_secs > 0 and \
                        (time.monotonic() - self._chunk_start_mono) >= chunk_secs:
                    try:
                        self._rotate_chunk()
                    except Exception:
                        pass   # non-fatal: keep recording in the old file

            if msg.arbitration_id not in self._frame_id_lookup:
                continue

            try:
                db_msg, decoded = self._db_decode(msg.arbitration_id, msg.data)
                now_str = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                with self._lock:
                    for full_name, sig_name in self._frame_id_lookup[msg.arbitration_id]:
                        if sig_name in decoded:
                            self.signal_latest_values[full_name] = decoded[sig_name]
                            self.signal_last_seen[full_name]     = now_str
            except Exception:
                pass
