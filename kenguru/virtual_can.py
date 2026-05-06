"""
kenguru.virtual_can
~~~~~~~~~~~~~~~~~~~
Synthetic CAN bus that generates random-walk signal values from a loaded DBC.

Mimics the ``can.Bus`` API (``recv`` / ``shutdown``) so the rest of the
application needs no special-casing for simulation vs. real hardware.
"""
from __future__ import annotations

import random
import threading
import time
from collections import deque

import can


class VirtualCANBus:
    """Fake CAN bus that synthesises messages for every frame defined in
    the loaded DBC.  Signal values perform a bounded random walk so
    live plots and the signal table show realistic-looking movement.

    Parameters
    ----------
    db:
        cantools database (must already be loaded).
    msg_rate_hz:
        How many CAN frames to emit per second (total).
    fd:
        When ``True``, emitted frames carry the CAN-FD flags.
    """

    def __init__(self, db, msg_rate_hz: float = 100.0, fd: bool = False):
        self._db          = db
        self._fd          = fd
        self._stop        = threading.Event()
        self._queue: deque[can.Message] = deque()
        self._lock        = threading.Lock()
        self._msg_rate_hz = max(1.0, msg_rate_hz)

        # Per-signal random-walk state: {full_name: (current, lo, hi)}
        self._state: dict[str, tuple[float, float, float]] = {}
        self._messages = list(db.messages)

        for msg in self._messages:
            for sig in msg.signals:
                lo  = sig.minimum if sig.minimum is not None else 0.0
                hi  = sig.maximum if sig.maximum is not None else 100.0
                if lo == hi:
                    hi = lo + 1.0
                mid = (lo + hi) / 2.0
                self._state[f"{msg.name}.{sig.name}"] = (mid, lo, hi)

        self._thread = threading.Thread(target=self._generate, daemon=True)
        self._thread.start()

    # ── Internal frame generator ─────────────────────────────────────

    def _generate(self) -> None:
        if not self._messages:
            return
        interval  = 1.0 / self._msg_rate_hz
        msg_count = len(self._messages)
        idx       = 0

        while not self._stop.is_set():
            msg = self._messages[idx % msg_count]
            idx += 1

            signal_vals: dict[str, float] = {}
            for sig in msg.signals:
                key       = f"{msg.name}.{sig.name}"
                cur, lo, hi = self._state[key]
                step      = (hi - lo) * 0.02          # 2 % of range per tick
                cur       = cur + random.uniform(-step, step)
                cur       = max(lo, min(hi, cur))      # clamp to [lo, hi]
                self._state[key] = (cur, lo, hi)
                signal_vals[sig.name] = cur

            try:
                data = msg.encode(signal_vals, padding=True)
            except Exception:
                data = bytes(msg.length)

            can_msg = can.Message(
                arbitration_id=msg.frame_id,
                data=data,
                is_extended_id=False,
                is_fd=self._fd,
                bitrate_switch=self._fd,
                timestamp=time.time(),
            )
            with self._lock:
                self._queue.append(can_msg)

            time.sleep(interval)

    # ── Public API matching can.Bus ───────────────────────────────────

    def recv(self, timeout: float = 1.0) -> can.Message | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._queue:
                    return self._queue.popleft()
            time.sleep(0.005)
        return None

    def shutdown(self) -> None:
        self._stop.set()

    def on_message_received(self, msg: can.Message) -> None:  # noqa: ARG002
        """Satisfy the BLFWriter integration contract; not used internally."""
