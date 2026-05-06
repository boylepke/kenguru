"""
kenguru.camera
~~~~~~~~~~~~~~
CameraManager: camera detection, the single background capture thread,
VideoWriter management, and the inline preview widget.

The manager owns all camera-related state so that camera logic can be
changed independently of the session and export subsystems.

Coupling note
~~~~~~~~~~~~~
``CameraManager`` holds a back-reference to the main ``CANLoggerApp``
instance (``self._app``) to access UI widgets (``cam_var``,
``cam_res_var``, ``cam_fps_var``, ``cam_quality_var``,
``record_video_var``, ``cam_status_label``, ``cam_preview_canvas``) and
to read the three recording-time stamps set by ``CANSession``
(``_first_can_msg_time``, ``_blf_start_time``, ``_record_start_mono``).
These dependencies are intentional and documented; further decoupling
(e.g. dependency-injection via callbacks) is straightforward in a future
iteration.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from tkinter import messagebox

try:
    import cv2
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False


class CameraManager:
    """Owns the camera capture thread, VideoWriter, and inline preview."""

    def __init__(self, app) -> None:
        self._app = app

        # ── Capture state ─────────────────────────────────────────
        self._cam_cap:          cv2.VideoCapture | None = None
        self._cam_thread:       threading.Thread | None = None
        self._cam_thread_stop:  threading.Event         = threading.Event()
        self._cam_current_index: int | None             = None
        self._cam_name_to_index: dict[str, int]         = {}

        # Rolling window of frame delivery timestamps for actual-fps measurement.
        # 90 frames gives a stable estimate without being too slow to converge.
        self._cam_frame_times: deque[float] = deque(maxlen=90)
        self._cam_actual_size: tuple[int, int] | None = None

        # ── Video writer ──────────────────────────────────────────
        self._video_writer:       cv2.VideoWriter | None   = None
        self._video_writer_lock:  threading.Lock           = threading.Lock()
        # Set by start_recording(); consumed and cleared by the camera thread
        # on the first real frame, guaranteeing writer dimensions == frame dims.
        self._pending_writer_info: tuple[str, float] | None = None

        # Sync metadata exposed to CANSession after stop_recording()
        self.last_video_filename:    str | None   = None
        self._video_first_frame_time: float | None = None
        self._video_frame_pts:        list[float]  = []

        # ── Inline preview ────────────────────────────────────────
        self._preview_running:         bool                = False
        self._cam_latest_frame                             = None   # numpy array or None
        self._cam_latest_frame_lock:   threading.Lock      = threading.Lock()

    # ── Camera detection ─────────────────────────────────────────────

    def detect_cameras(self) -> None:
        """Probe indices 0-9, populate the dropdown, open the first camera.

        All caps found during probing are kept open simultaneously so that
        cameras with a USB idle-sleep bug (ELP etc.) are never released then
        re-opened mid-probe.
        """
        if not _CV2_AVAILABLE:
            return
        self.release_camera()

        # Friendly names from Windows Device Manager
        device_names: list[str] = []
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-PnpDevice -Class Camera -Status OK | "
                 "Select-Object -ExpandProperty FriendlyName"],
                capture_output=True, text=True, timeout=5,
            )
            device_names = [n.strip() for n in result.stdout.strip().splitlines() if n.strip()]
        except Exception:
            pass

        # Suppress DirectShow noise while probing
        found_indices: list[int] = []
        found_caps:   dict[int, cv2.VideoCapture] = {}
        devnull = open(os.devnull, "w")
        old_fd  = os.dup(2)
        os.dup2(devnull.fileno(), 2)
        try:
            for i in range(10):
                cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                if cap.isOpened():
                    found_indices.append(i)
                    found_caps[i] = cap
        finally:
            os.dup2(old_fd, 2)
            os.close(old_fd)
            devnull.close()

        status_label = getattr(self._app, "cam_status_label", None)
        dropdown     = getattr(self._app, "cam_dropdown", None)
        cam_var      = getattr(self._app, "cam_var", None)

        if not found_indices:
            if dropdown:
                dropdown["values"] = []
            if cam_var:
                cam_var.set("None detected")
            self._cam_name_to_index = {}
            if status_label:
                status_label.config(text="No cameras found", foreground="red")
            return

        self._cam_name_to_index = {}
        display_names: list[str] = []
        for i, idx in enumerate(found_indices):
            label = device_names[i] if i < len(device_names) else f"Camera {idx}"
            display_names.append(label)
            self._cam_name_to_index[label] = idx

        if dropdown:
            dropdown["values"] = display_names
        if cam_var:
            cam_var.set(display_names[0])

        # Hand the already-open cap to the thread; release the rest.
        selected_idx = found_indices[0]
        keeper_cap   = found_caps.pop(selected_idx)
        for leftover in found_caps.values():
            leftover.release()

        self._open_camera_thread(selected_idx, keeper_cap)

    def on_camera_selected(self, event=None) -> None:
        """Switch camera when the user picks a different one in the dropdown."""
        if not _CV2_AVAILABLE:
            return
        settings = self._parse_cam_settings()
        if settings is None:
            return
        new_index = settings[0]
        if self._cam_current_index == new_index:
            return
        self.release_camera()
        self._open_camera_thread(new_index)

    # ── Single background camera thread ──────────────────────────────

    def _open_camera_thread(self, index: int,
                            existing_cap: cv2.VideoCapture | None = None) -> None:
        """Start the background capture thread.

        If *existing_cap* is supplied (kept open from the detect probe) we
        configure it in-place instead of opening a new handle — this is the
        zero-gap path that avoids ELP sleep resets.
        """
        settings = self._parse_cam_settings()
        w   = int(settings[1]) if settings else 1280
        h   = int(settings[2]) if settings else 720
        fps = int(settings[3]) if settings else 30

        cap = existing_cap if existing_cap is not None else \
              cv2.VideoCapture(index, cv2.CAP_DSHOW)

        cap.set(cv2.CAP_PROP_BUFFERSIZE,    1)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,   w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT,  h)
        cap.set(cv2.CAP_PROP_FPS,           fps)

        status_label = getattr(self._app, "cam_status_label", None)
        if not cap.isOpened():
            if status_label:
                status_label.config(text="Could not open camera", foreground="red")
            return

        self._cam_cap          = cap
        self._cam_current_index = index
        self._cam_actual_size  = None
        self._cam_frame_times.clear()
        self._cam_thread_stop  = threading.Event()

        def _loop() -> None:
            while not self._cam_thread_stop.is_set():
                ret, frame = self._cam_cap.read()
                if not ret:
                    time.sleep(0.05)
                    continue
                fh, fw = frame.shape[:2]
                self._cam_frame_times.append(time.monotonic())
                if self._cam_actual_size is None:
                    self._cam_actual_size = (fw, fh)
                with self._cam_latest_frame_lock:
                    self._cam_latest_frame = frame

                # Create VideoWriter from the first real frame so dimensions
                # are guaranteed to match regardless of DirectShow negotiation.
                with self._video_writer_lock:
                    if self._pending_writer_info is not None and self._video_writer is None:
                        stem, rec_fps = self._pending_writer_info
                        self._pending_writer_info = None
                        self._video_writer = self._create_writer(stem, rec_fps, fw, fh)

                with self._video_writer_lock:
                    w_ref = self._video_writer
                    if w_ref is not None:
                        if self._video_first_frame_time is None:
                            self._video_first_frame_time = time.time()

                        frame_out = self._draw_timecode(frame.copy())
                        w_ref.write(frame_out)
                        session = getattr(self._app, "session", None)
                        rec_start = getattr(session, "_record_start_mono", 0.0) if session else 0.0
                        self._video_frame_pts.append(time.monotonic() - rec_start)

        self._cam_thread = threading.Thread(target=_loop, daemon=True)
        self._cam_thread.start()
        if status_label:
            status_label.config(text="Camera open — keep-alive active",
                                foreground="green")

    def _create_writer(self, stem: str, fps: float,
                       fw: int, fh: int) -> cv2.VideoWriter | None:
        """Try codec candidates in order; return the first that opens."""
        _q_map = {
            "Low (smallest)": 25,
            "Medium":         50,
            "High":           75,
            "Max (largest)":  95,
        }
        quality_var = getattr(self._app, "cam_quality_var", None)
        _quality = _q_map.get(quality_var.get() if quality_var else "Medium", 50)

        codec_candidates = [
            ("MJPG", stem + ".avi"),
            ("mp4v", stem + ".mp4"),
            ("XVID", stem + ".avi"),
            (0,      stem + "_raw.avi"),
        ]
        for fourcc_val, vpath in codec_candidates:
            fourcc = cv2.VideoWriter_fourcc(*fourcc_val) \
                     if isinstance(fourcc_val, str) else int(fourcc_val)
            w_try = cv2.VideoWriter(vpath, fourcc, fps, (fw, fh))
            if w_try.isOpened():
                w_try.set(cv2.VIDEOWRITER_PROP_QUALITY, _quality)
                self.last_video_filename = vpath
                return w_try
            w_try.release()
        return None

    def _draw_timecode(self, frame) -> object:
        """Burn a CAN-relative timecode into the top-left corner of *frame*."""
        session = getattr(self._app, "session", None)
        first_can = getattr(session, "_first_can_msg_time", None) if session else None
        blf_start = getattr(session, "_blf_start_time", 0.0) if session else 0.0

        tc_ref = first_can if first_can is not None else (blf_start if blf_start > 0 else None)
        tc_s   = (time.time() - tc_ref) if tc_ref is not None else 0.0
        tc_str = f"CAN  {int(tc_s // 60):02d}:{tc_s % 60:06.3f}"

        font       = cv2.FONT_HERSHEY_DUPLEX
        font_scale = 0.65
        thickness  = 2
        (tw, th), baseline = cv2.getTextSize(tc_str, font, font_scale, thickness)

        pad = 6
        x, y = 10, 10
        cv2.rectangle(frame,
                      (x - pad, y - pad),
                      (x + tw + pad, y + th + pad + baseline),
                      (0, 0, 0), -1)
        cv2.putText(frame, tc_str, (x, y + th),
                    font, font_scale, (173, 207, 72), thickness, cv2.LINE_AA)
        return frame

    def release_camera(self) -> None:
        """Fully stop the camera thread and release the capture handle."""
        self._cam_thread_stop.set()
        if self._cam_thread and self._cam_thread.is_alive():
            self._cam_thread.join(timeout=3)
        self._cam_thread = None
        with self._video_writer_lock:
            if self._video_writer:
                self._video_writer.release()
                self._video_writer = None
        if self._cam_cap:
            self._cam_cap.release()
            self._cam_cap = None

    # ── Video recording control ───────────────────────────────────────

    def start_recording(self, base_filename: str) -> None:
        """Instruct the camera thread to create a VideoWriter for *base_filename*."""
        if not _CV2_AVAILABLE:
            return
        record_video_var = getattr(self._app, "record_video_var", None)
        if record_video_var and not record_video_var.get():
            return
        if self._cam_cap is None or not self._cam_cap.isOpened():
            messagebox.showwarning(
                "Camera", "Camera is not open. Click Detect Cameras first.")
            return

        # Measure actual fps from the rolling timestamp deque.
        ft = self._cam_frame_times
        if len(ft) >= 10 and (ft[-1] - ft[0]) > 0.1:
            actual_fps = (len(ft) - 1) / (ft[-1] - ft[0])
        else:
            actual_fps = self._cam_cap.get(cv2.CAP_PROP_FPS)
        settings = self._parse_cam_settings()
        if actual_fps <= 0:
            actual_fps = float(settings[3]) if settings else 30.0
        actual_fps = max(1.0, float(actual_fps))

        self._video_first_frame_time = None
        self._video_frame_pts        = []

        stem = os.path.splitext(base_filename)[0]
        with self._video_writer_lock:
            self._pending_writer_info = (stem, actual_fps)

    def stop_recording(self) -> tuple[str | None, list[float], float | None]:
        """Detach the VideoWriter; thread continues in keep-alive mode.

        Returns
        -------
        (last_video_filename, video_frame_pts, video_first_frame_time)
        """
        if not _CV2_AVAILABLE:
            return None, [], None
        with self._video_writer_lock:
            w = self._video_writer
            self._video_writer         = None
            self._pending_writer_info  = None
        if w:
            w.release()
        return self.last_video_filename, self._video_frame_pts, self._video_first_frame_time

    # ── Inline preview ────────────────────────────────────────────────

    def open_preview(self) -> None:
        """Toggle the embedded 160×90 live preview in the operation frame."""
        if not _CV2_AVAILABLE:
            return
        if self._preview_running:
            self._stop_preview()
            return
        if self._cam_cap is None or not self._cam_cap.isOpened():
            messagebox.showwarning("Warning", "No camera open.\nRun Detect Cameras first.")
            return

        self._preview_running = True
        canvas = getattr(self._app, "cam_preview_canvas", None)
        root   = getattr(self._app, "root", None)

        try:
            from PIL import Image, ImageTk

            def _update() -> None:
                if not self._preview_running or canvas is None:
                    return
                with self._cam_latest_frame_lock:
                    frame = self._cam_latest_frame
                if frame is not None:
                    fh, fw = frame.shape[:2]
                    cw, ch = 160, 90
                    scale  = min(cw / max(fw, 1), ch / max(fh, 1))
                    pw = max(1, int(fw * scale))
                    ph = max(1, int(fh * scale))
                    frame_s = cv2.resize(frame, (pw, ph), interpolation=cv2.INTER_LINEAR)
                    frame_r = cv2.cvtColor(frame_s, cv2.COLOR_BGR2RGB)
                    img = ImageTk.PhotoImage(Image.fromarray(frame_r))
                    canvas.delete("all")
                    canvas.create_image(cw // 2, ch // 2, anchor="center", image=img)
                    canvas._img = img  # prevent garbage-collector flicker
                if root:
                    root.after(33, _update)

            _update()

        except ImportError:
            from . import theme as th
            if canvas:
                canvas.delete("all")
                canvas.create_text(
                    80, 45,
                    text="pip install Pillow\nfor preview",
                    fill=th.MUTED, font=("Consolas", 9), justify="center",
                )

    def _stop_preview(self) -> None:
        """Stop the preview after-loop and restore the placeholder text."""
        from . import theme as th
        self._preview_running = False
        canvas = getattr(self._app, "cam_preview_canvas", None)
        if canvas:
            canvas.delete("all")
            canvas.create_text(
                80, 45, text="No Preview",
                fill=th.MUTED, font=("Consolas", 9), tags="placeholder",
            )

    # ── Helpers ───────────────────────────────────────────────────────

    def _parse_cam_settings(self) -> tuple[int, int, int, int] | None:
        """Return ``(index, width, height, fps)`` from current UI selections."""
        cam_var = getattr(self._app, "cam_var", None)
        if cam_var is None:
            return None
        name = cam_var.get()
        if name in self._cam_name_to_index:
            index = self._cam_name_to_index[name]
        else:
            try:
                index = int(name)
            except ValueError:
                return None
        res_var = getattr(self._app, "cam_res_var", None)
        fps_var = getattr(self._app, "cam_fps_var", None)
        if res_var is None or fps_var is None:
            return None
        w, h = map(int, res_var.get().split("x"))
        fps  = int(fps_var.get())
        return index, w, h, fps
