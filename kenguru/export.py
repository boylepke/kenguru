"""
kenguru.export
~~~~~~~~~~~~~~
ExportManager: save BLF, write session-info sidecar, export MF4, export CSV.

All methods are driven from the main-window toolbar buttons and operate
on the last recorded session data held by ``CANSession``.
"""
from __future__ import annotations

import csv
import os
import shutil
from datetime import datetime
from tkinter import filedialog, messagebox

import can


class ExportManager:
    """Handles all file-export operations for the application."""

    def __init__(self, app) -> None:
        self._app = app

    # ── Session metadata ─────────────────────────────────────────────

    def get_metadata(self) -> dict[str, str]:
        prefs = self._app.prefs_mgr.prefs
        return {
            "Project":       prefs.get("meta_project", ""),
            "Vehicle":       prefs.get("meta_vehicle", ""),
            "Driver":        prefs.get("meta_driver",  ""),
            "Configuration": prefs.get("meta_config",  ""),
            "Comment":       prefs.get("meta_comment", ""),
        }

    def write_sidecar_txt(self, base_path: str) -> None:
        """Write a human-readable ``_info.txt`` sidecar alongside *base_path*."""
        txt_path = os.path.splitext(base_path)[0] + "_info.txt"
        meta     = self.get_metadata()
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write("Kenguru CAN Monitor — Session Info\n")
                f.write(f"Recorded: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("-" * 40 + "\n")
                for key, val in meta.items():
                    f.write(f"{key:<16}: {val}\n")
        except Exception as e:
            messagebox.showwarning("Warning",
                                   f"Could not write session info sidecar:\n{e}")

    # ── Save BLF ─────────────────────────────────────────────────────

    def save_blf(self) -> None:
        """Copy the last recorded BLF (and companion files) to a user location."""
        session = self._app.session
        if not session.last_blf_filename or not os.path.exists(session.last_blf_filename):
            messagebox.showwarning(
                "Warning",
                "No recorded BLF file available.\nPlease record a session first.",
            )
            return

        save_path = filedialog.asksaveasfilename(
            defaultextension=".blf",
            filetypes=[("BLF files", "*.blf"), ("All files", "*.*")],
            initialfile=os.path.basename(session.last_blf_filename),
        )
        if not save_path:
            return

        try:
            shutil.copy2(session.last_blf_filename, save_path)
            self.write_sidecar_txt(save_path)

            save_stem = os.path.splitext(save_path)[0]
            src_stem  = os.path.splitext(session.last_blf_filename)[0]

            for ext in (".pts", ".sync"):
                src = src_stem + ext
                if os.path.exists(src):
                    shutil.copy2(src, save_stem + ext)

            _copied_video = None
            for _ext in (".avi", ".mp4", "_raw.avi"):
                vsrc = src_stem + _ext
                if os.path.exists(vsrc):
                    vdst = save_stem + _ext
                    shutil.copy2(vsrc, vdst)
                    _copied_video = vdst
                    break

            _info_path = f"{save_stem}_info.txt"
            _extra     = ""
            if os.path.exists(save_stem + ".pts"):
                _extra += f"\nSync (pts): {save_stem}.pts"
            elif os.path.exists(save_stem + ".sync"):
                _extra += f"\nSync file:  {save_stem}.sync"
            if _copied_video:
                _extra += f"\nVideo:      {_copied_video}"

            messagebox.showinfo(
                "Success",
                f"BLF saved to:\n{save_path}\n"
                f"Session info: {_info_path}{_extra}",
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save BLF file:\n{e}")

    # ── Export MF4 ───────────────────────────────────────────────────

    def export_mf4(self) -> None:
        """Convert the last recorded BLF to MF4 using asammdf."""
        session = self._app.session
        if not session.last_blf_filename or not os.path.exists(session.last_blf_filename):
            messagebox.showwarning(
                "Warning",
                "No recorded BLF file available.\nPlease record a session first.",
            )
            return
        if not session.dbs:
            messagebox.showwarning(
                "Warning",
                "No DBC loaded.\nA DBC is required to decode signals for MF4 export.",
            )
            return

        try:
            import asammdf
            import numpy as np
        except ImportError as e:
            messagebox.showerror(
                "Error", f"Missing library: {e}\nRun: pip install asammdf numpy")
            return

        save_path = filedialog.asksaveasfilename(
            defaultextension=".mf4",
            filetypes=[("MF4 files", "*.mf4"), ("All files", "*.*")],
            initialfile=os.path.splitext(
                os.path.basename(session.last_blf_filename))[0] + ".mf4",
        )
        if not save_path:
            return

        try:
            series: dict = {}
            with can.BLFReader(session.last_blf_filename) as reader:
                t0 = reader.start_timestamp
                for msg in reader:
                    try:
                        db_msg, decoded = session._db_decode(msg.arbitration_id, msg.data)
                    except Exception:
                        continue
                    for sig_name, value in decoded.items():
                        full_name = f"{db_msg.name}.{sig_name}"
                        if full_name not in series:
                            series[full_name] = ([], [])
                        series[full_name][0].append(msg.timestamp - t0)
                        series[full_name][1].append(float(value))

            if not series:
                messagebox.showwarning(
                    "Warning",
                    "No decodable signals found in the BLF file.\n"
                    "Make sure the correct DBC is loaded.")
                return

            signals = []
            for full_name, (timestamps, values) in series.items():
                unit = ""
                try:
                    parts = full_name.split(".", 1)
                    for _, db in session.dbs:
                        try:
                            db_msg = db.get_message_by_name(parts[0])
                            db_sig = db_msg.get_signal_by_name(parts[1])
                            unit   = db_sig.unit or ""
                            break
                        except Exception:
                            continue
                except Exception:
                    pass
                signals.append(asammdf.Signal(
                    name=full_name,
                    samples=np.array(values, dtype=np.float64),
                    timestamps=np.array(timestamps, dtype=np.float64),
                    unit=unit,
                ))

            mdf  = asammdf.MDF(version="4.10")
            mdf.append(signals, common_timebase=False)
            meta = self.get_metadata()
            mdf.header.author     = meta["Driver"]
            mdf.header.project    = meta["Project"]
            mdf.header.subject    = meta["Vehicle"]
            mdf.header.department = meta["Configuration"]
            mdf.header.comment    = meta["Comment"]
            mdf.save(save_path, overwrite=True)
            messagebox.showinfo(
                "Success",
                f"MF4 exported with {len(signals)} signals to:\n{save_path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export MF4:\n{e}")

    # ── Export CSV ───────────────────────────────────────────────────

    def export_csv(self) -> None:
        """Prompt the user to choose between snapshot and time-series CSV."""
        choice = messagebox.askyesnocancel(
            "Export CSV",
            "Which export would you like?\n\n"
            "  Yes   → Time-series  (full recorded data from BLF, one row per frame)\n"
            "  No    → Snapshot     (current last-seen value for each signal)\n"
            "  Cancel → Abort",
        )
        if choice is None:
            return
        if choice:
            self._export_csv_timeseries()
        else:
            self._export_csv_snapshot()

    def _export_csv_snapshot(self) -> None:
        """Export the live signal snapshot (last-seen values)."""
        session = self._app.session
        with session._lock:
            snapshot  = dict(session.signal_latest_values)
            last_seen = dict(session.signal_last_seen)

        if not snapshot:
            messagebox.showwarning("Warning", "No signal data to export yet.")
            return

        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=f"CAN_Snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        )
        if not save_path:
            return

        try:
            with open(save_path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["Signal", "Value", "Unit", "Last Seen"])
                for full_name, value in snapshot.items():
                    info     = session.selected_signals.get(full_name, {})
                    decimals = info.get("decimals", 2)
                    try:
                        formatted = f"{float(value):.{decimals}f}"
                    except (TypeError, ValueError):
                        formatted = str(value)
                    w.writerow([full_name, formatted,
                                info.get("unit", ""),
                                last_seen.get(full_name, "")])
            messagebox.showinfo("Success", f"Snapshot CSV exported to:\n{save_path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export snapshot CSV:\n{e}")

    def _export_csv_timeseries(self) -> None:
        """Decode the last BLF and write a wide time-series CSV."""
        session = self._app.session
        if not session.dbs:
            messagebox.showwarning(
                "Warning", "No DBC loaded — a DBC is needed to decode the BLF.")
            return

        blf_path = session.last_blf_filename
        if not blf_path or not os.path.exists(blf_path):
            blf_path = filedialog.askopenfilename(
                title="Open BLF for CSV export",
                filetypes=[("BLF files", "*.blf"), ("All files", "*.*")],
            )
            if not blf_path:
                return

        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=os.path.splitext(os.path.basename(blf_path))[0] + "_timeseries.csv",
        )
        if not save_path:
            return

        try:
            rows:         list[dict]  = []
            signals_seen: list[str]   = []
            t0 = None

            with can.BLFReader(blf_path) as reader:
                t0 = reader.start_timestamp
                for msg in reader:
                    rel_t = msg.timestamp - t0
                    try:
                        db_msg, decoded = session._db_decode(msg.arbitration_id, msg.data)
                    except Exception:
                        continue
                    row = {"Time (s)": f"{rel_t:.6f}", "Message": db_msg.name}
                    for sig_name, value in decoded.items():
                        full_name = f"{db_msg.name}.{sig_name}"
                        if full_name not in signals_seen:
                            signals_seen.append(full_name)
                        info     = session.selected_signals.get(full_name, {})
                        decimals = info.get("decimals", 6)
                        scale    = info.get("scale",    1.0)
                        offset   = info.get("offset",   0.0)
                        try:
                            converted    = float(value) * scale + offset
                            row[full_name] = f"{converted:.{decimals}f}"
                        except (TypeError, ValueError):
                            row[full_name] = str(value)
                    rows.append(row)

            if not rows:
                messagebox.showwarning(
                    "Warning",
                    "No decodable frames found in the BLF.\n"
                    "Make sure the correct DBC is loaded.")
                return

            headers = ["Time (s)", "Message"] + signals_seen
            with open(save_path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)

            messagebox.showinfo(
                "Success",
                f"Time-series CSV exported.\n"
                f"{len(rows)} rows  |  {len(signals_seen)} signals\n{save_path}",
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export time-series CSV:\n{e}")
