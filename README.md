# Kenguru CAN Monitor

A Windows desktop tool for synchronized CAN bus data recording and video capture.
Records BLF files, burns per-frame timecode overlays, and exports to BLF, MF4, and CSV.

Built with Python · Tkinter · python-can · cantools · OpenCV

---

## Features

- **Multi-DBC support** — load several `.dbc` files simultaneously; signals decoded from all
- **Synchronized video** — single background camera thread writes frames with a CAN-relative timecode overlay
- **Sub-frame sync** — `.pts` per-frame timestamp file eliminates fps-drift errors at review time
- **Virtual CAN bus** — simulate traffic from any loaded DBC without hardware
- **CAN-FD** — Vector interface supports both classic CAN and CAN-FD
- **Review window** — interactive signal plot + video playback with slider and tooltip
- **Export** — BLF copy, MF4 (via asammdf), CSV snapshot, CSV time-series
- **Themes** — six built-in colour presets, fully customisable per-slot

---

## Project structure

```
kenguru_can_monitor/
├── main.py                  # Entry point  — python main.py
├── requirements.txt
└── kenguru/
    ├── __init__.py
    ├── theme.py             # Colour constants + ttk style helpers
    ├── virtual_can.py       # VirtualCANBus simulator
    ├── preferences.py       # PreferencesManager: load/save/dialog
    ├── camera.py            # CameraManager: detect, capture thread, writer, preview
    ├── can_session.py       # CANSession: bus, DBC, recording lifecycle, receive loop
    ├── signal_selector.py   # Signal-selection Toplevel
    ├── review_window.py     # Post-recording review Toplevel
    ├── export.py            # ExportManager: BLF copy, MF4, CSV
    └── app.py               # CANLoggerApp main window — wires everything together
```

---

## Installation

```bash
git clone https://github.com/<your-org>/kenguru-can-monitor.git
cd kenguru-can-monitor
pip install -r requirements.txt
python main.py
```

> **Windows only** — the camera detection uses PowerShell (`Get-PnpDevice`) and
> DirectShow (`cv2.CAP_DSHOW`).  Vector and Canalyst-II drivers must be installed
> separately.

### Optional dependencies

| Feature     | Package           |
|-------------|-------------------|
| MF4 export  | `asammdf numpy`   |
| Video preview | `Pillow`        |

---

## Supported hardware

| Interface    | Notes                                     |
|-------------|-------------------------------------------|
| Vector       | Classic CAN + CAN-FD; auto-detects channels |
| Canalyst-II  | Classic CAN only; channels 0 and 1        |
| Virtual CAN  | Software simulation from loaded DBC        |

---

## Cameras

Any DirectShow-compatible webcam.  The Logitech C920 is the reference camera:
it reliably detects via `cv2.CAP_DSHOW`, supports 720p/1080p, and the app's
90-frame rolling FPS measurement compensates for its known DirectShow reporting
inaccuracy.

---

## Architecture notes

Each subsystem lives in its own module and can be modified independently:

- **`theme.py`** — change colours or add presets without touching any other file
- **`camera.py`** — swap codec, add GPU encoding, or change overlay format here
- **`can_session.py`** — add new interfaces or change BLF options here
- **`review_window.py`** — add plot channels, export clips, or a second signal trace here
- **`export.py`** — add new export formats here

---

## License

MIT
