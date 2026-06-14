"""
kenguru.can_session
~~~~~~~~~~~~~~~~~~~
CANSession: CAN bus connection, DBC loading, recording lifecycle, and the
background receive loop.

Chunk recording
~~~~~~~~~~~~~~~
When ``prefs["chunk_duration"]`` is non-zero, the receive loop calls
``_rotate_chunk()`` every N seconds.  Each completed chunk gets its sync
files, sidecar, optional MF4 conversion, and optional robocopy trigger.
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
        self._all_buses: list = []    # all open bus instances (for dual-channel)
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
        self._chunk_start_mono: float = 0.0

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
                    "Load anyway in non-strict mode?",
                )
                if not ans:
                    continue
                try:
                    db = cantools.database.load_file(path, strict=False)
                    non_strict = True
                except Exception as e2:
                    messagebox.showerror("Error",
                        f"Failed to load DBC even in non-strict mode:\n{path}\n\n{e2}")
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
            for _, db in self.dbs for msg in db.messages for sig in msg.signals
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
        self._all_buses = []
        try:
            if interface == "Virtual CAN":
                if not self.dbs:
                    messagebox.showwarning("Warning",
                        "Load a DBC file first — the virtual bus needs it to generate frames.")
                    return
                class _CombinedDB:
                    def __init__(self, dbs):
                        self.messages = [m for _, db in dbs for m in db.messages]
                self.bus = VirtualCANBus(
                    _CombinedDB(self.dbs), msg_rate_hz=100.0, fd=fd_mode)
                self._all_buses = [self.bus]
                if not silent:
                    messagebox.showinfo("Success",
                        f"Virtual CAN bus started ({'CAN-FD' if fd_mode else 'classic CAN'}).")
                return

            bitrate = int(self._app.bitrate_var.get()) * 1000
            channel = int(self._app.channel_var.get())

            if interface == "Vector":
                if fd_mode:
                    try:
                        data_bitrate = int(self._app.fd_data_bitrate_var.get()) * 1000
                    except ValueError:
                        messagebox.showerror("Error",
                            "Invalid data bitrate — enter a number in kbps (e.g. 2000).")
                        return
                    self.bus = can.interface.Bus(
                        interface="vector", channel=channel,
                        bitrate=bitrate, data_bitrate=data_bitrate, fd=True)
                    if not silent:
                        messagebox.showinfo("Success",
                            f"Connected to Vector in CAN-FD mode.\n"
                            f"Arb: {bitrate//1000} kbps  |  Data: {data_bitrate//1000} kbps")
                else:
                    self.bus = can.interface.Bus(
                        interface="vector", channel=channel, bitrate=bitrate)
                    if not silent:
                        messagebox.showinfo("Success",
                            f"Connected to Vector (classic CAN, {bitrate//1000} kbps).")
                self._all_buses = [self.bus]

            elif interface == "Canalyst-II":
                # Open both channels simultaneously
                bus_ch0 = can.interface.Bus(
                    interface="canalystii", channel=0, bitrate=bitrate)
                bus_ch1 = can.interface.Bus(
                    interface="canalystii", channel=1, bitrate=bitrate)
                self.bus = bus_ch0              # primary reference
                self._all_buses = [bus_ch0, bus_ch1]
                if not silent:
                    messagebox.showinfo("Success",
                        f"Connected to Canalyst-II, both channels ({bitrate//1000} kbps).")

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
        self._blf_start_time          = time.time()
        self._first_can_msg_time      = None
        self._first_can_msg_wall_time = None
        self._record_start_mono       = time.monotonic()
        self._first_can_msg_mono      = None
        self._chunk_start_mono        = time.monotonic()

    # ── Sync file helpers ─────────────────────────────────────────────

    def _write_sync_files(self, blf_path: str, blf_t0,
                          frame_pts: list, first_frame_time) -> None:
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

    # ── Post-chunk processing ─────────────────────────────────────────

    def _finalise_chunk(self, blf_path: str, blf_t0,
                        frame_pts: list, first_frame_time) -> None:
        """Write sync files, sidecar, auto-MF4, and trigger robocopy
        for a completed chunk.  Called by both ``stop()`` and
        ``_rotate_chunk()``.
        """
        self._write_sync_files(blf_path, blf_t0, frame_pts, first_frame_time)
        if os.path.exists(blf_path):
            self._app.export_mgr.write_sidecar_txt(blf_path)
        # Auto-convert to MF4 if enabled
        if self._app.prefs_mgr.prefs.get("auto_mf4", False):
            try:
                self._app.export_mgr.auto_convert_mf4(blf_path)
            except Exception:
                pass
        # Robocopy trigger
        self._maybe_run_robocopy()

    def _maybe_run_robocopy(self) -> None:
        pm  = self._app.prefs_mgr
        src = pm.prefs.get("rcopy_src", "").strip() or pm.prefs.get("save_dir", "")
        if pm.should_run_robocopy(src):
            pm.run_robocopy()

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
            for b in self._all_buses:
                try:
                    b.shutdown()
                except Exception:
                    pass
            self.bus = None
            self._all_buses = []

        self._app.set_status("idle")
        self._rec_size_history.clear()
        self._app.rec_stats_label.pack_forget()

        vid_file, frame_pts, first_frame_time = self._app.camera.stop_recording()

        if was_recording and self.last_blf_filename:
            self._finalise_chunk(
                self.last_blf_filename, blf_t0, frame_pts, first_frame_time)

    # ── Chunk rotation ────────────────────────────────────────────────

    def _rotate_chunk(self) -> None:
        """Close the current chunk and open the next one."""
        blf_t0 = getattr(self.blf_writer, "start_timestamp", None)
        self.blf_writer.stop()

        vid_file, frame_pts, first_frame_time = self._app.camera.stop_recording()

        self._finalise_chunk(
            self.last_blf_filename, blf_t0, frame_pts, first_frame_time)

        new_filename           = self._next_filename()
        self.last_blf_filename = new_filename
        self._reset_timing()

        self.blf_writer = can.BLFWriter(new_filename)
        self._app.camera.start_recording(new_filename)

    # ── Receive loop ─────────────────────────────────────────────────

    def receive_loop(self) -> None:
        buses = self._all_buses or ([self.bus] if self.bus else [])
        if not buses:
            return

        # Drain stale hardware buffers on ALL buses
        if self.recording:
            for b in buses:
                while True:
                    try:
                        stale = b.recv(timeout=0)
                    except Exception:
                        break
                    if stale is None:
                        break

        chunk_secs = self._app.prefs_mgr.prefs.get("chunk_duration", 0)

        # Short timeout per bus so we cycle through all channels quickly.
        # With N buses, worst-case latency per message = N * poll_timeout.
        poll_timeout = 0.05 if len(buses) > 1 else 1.0

        while not self._stop_event.is_set():
            got_any = False

            for bus in buses:
                if self._stop_event.is_set():
                    return
                try:
                    msg = bus.recv(timeout=poll_timeout)
                except can.CanError as e:
                    if not self._stop_event.is_set():
                        self._app.root.after(
                            0, lambda err=e: messagebox.showerror(
                                "CAN Error", f"Bus error in receive loop:\n{err}"))
                    return
                except Exception as e:
                    if not self._stop_event.is_set():
                        self._app.root.after(
                            0, lambda err=e: messagebox.showerror(
                                "Error", f"Unexpected error in receive loop:\n{err}"))
                    return

                if msg is None:
                    continue
                got_any = True

                if self.recording and self.blf_writer:
                    self._record_go.wait()
                    self.blf_writer.on_message_received(msg)

                    if self._first_can_msg_time is None:
                        self._first_can_msg_time      = msg.timestamp
                        self._first_can_msg_wall_time = time.time()
                        self._first_can_msg_mono      = \
                            time.monotonic() - self._record_start_mono

                    # Chunk boundary check
                    if chunk_secs > 0 and \
                            (time.monotonic() - self._chunk_start_mono) >= chunk_secs:
                        try:
                            self._rotate_chunk()
                        except Exception:
                            pass

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

            # Avoid busy-loop when no messages arrive on any bus
            if not got_any and len(buses) > 1:
                time.sleep(0.001)
