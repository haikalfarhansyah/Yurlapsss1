"""Subscriber ZeroMQ untuk decoder OpenStint (satu-satunya sumber data lap).

Protokol (lihat docs/decoder-protocol.md di repo zsellera/openstint):

    P <ts_ms> <type OPN|AMB> <id> <rssi> <hit_count> <pass_duration_us> <mer>
    T <ts_ms> <type> <id> <transponder_timecode>
    S <ts_ms> <noise_power> <dc_offset> <frames_received> <frames_processed> <jitter_p95_ms>

Pesan dipisah spasi dan bertipe posisional, jadi parsing dibuat defensif
(tidak unpack seluruh baris).
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from .engine import Engine, Passing


class DecoderClient(threading.Thread):
    def __init__(self, engine: Engine, endpoint: str, poll_ms: int = 250):
        super().__init__(name="decoder-zmq", daemon=True)
        self.engine = engine
        self.endpoint = endpoint
        self.poll_ms = poll_ms
        self._stop_event = threading.Event()
        self.connected = False
        self._got_message = False
        self._last_error: Optional[str] = None
        self.rx_counts = {"P": 0, "S": 0, "T": 0, "bad": 0}

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------
    def run(self) -> None:
        try:
            import zmq  # type: ignore
        except ImportError:
            self.engine.log("error", "pyzmq tidak terpasang — decoder tidak bisa dibaca "
                                     "(pip install pyzmq)")
            return

        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.SUB)
        sock.setsockopt(zmq.SUBSCRIBE, b"")
        sock.setsockopt(zmq.LINGER, 0)
        sock.setsockopt(zmq.RCVTIMEO, self.poll_ms)
        sock.connect(self.endpoint)

        last_connect_note = 0.0
        while not self._stop_event.is_set():
            try:
                msg = sock.recv_string()
            except zmq.Again:
                if not self._got_message and time.monotonic() - last_connect_note > 20:
                    last_connect_note = time.monotonic()
                    self.engine.log(
                        "info", f"Menunggu decoder di {self.endpoint} …")
                continue
            except Exception as exc:  # pragma: no cover
                self._last_error = str(exc)
                self.engine.log("error", f"ZeroMQ: {exc}")
                time.sleep(0.5)
                continue

            self._dispatch_message(msg)

        try:
            sock.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _dispatch_message(self, msg: str) -> None:
        if not self._got_message:
            self._got_message = True
            self.connected = True
            self.engine.log("ok", f"Data decoder diterima dari {self.endpoint}")
        parts = msg.split()
        if not parts:
            return
        kind = parts[0]
        try:
            if kind == "P":
                if len(parts) < 7:
                    self.rx_counts["bad"] += 1
                    return
                self.rx_counts["P"] += 1
                p = Passing(
                    transponder_id=int(parts[3]),
                    transponder_type=parts[2],
                    rssi=_f(parts, 4),
                    hit_count=_i(parts, 5),
                    pass_duration_us=_i(parts, 6),
                    mer=_f(parts, 7),
                    decoder_timecode=int(parts[1]),
                    raw=msg,
                )
                self.engine.on_passing(p)
            elif kind == "S":
                self.rx_counts["S"] += 1
                self.engine.decoder_status_message(parts)
            elif kind == "T":
                self.rx_counts["T"] += 1
                self.engine.on_timesync(parts)
            else:
                self.rx_counts["bad"] += 1
        except (ValueError, IndexError):
            self.rx_counts["bad"] += 1


def _i(parts: list[str], idx: int) -> Optional[int]:
    try:
        return int(parts[idx])
    except (IndexError, ValueError):
        return None


def _f(parts: list[str], idx: int) -> Optional[float]:
    try:
        return float(parts[idx])
    except (IndexError, ValueError):
        return None


def parse_passing(msg: str) -> Optional[Passing]:
    """Helper untuk tes/utilitas."""
    parts = msg.split()
    if len(parts) < 4 or parts[0] != "P":
        return None
    try:
        timestamp = int(parts[1])
        transponder = int(parts[3])
    except (TypeError, ValueError):
        # Malformed decoder lines are ignored by the stream worker; keep the
        # public helper equally safe for callers parsing logs manually.
        return None
    if timestamp < 0 or transponder < 0:
        return None
    return Passing(
        transponder_id=transponder,
        transponder_type=parts[2],
        rssi=_f(parts, 4),
        hit_count=_i(parts, 5),
        pass_duration_us=_i(parts, 6),
        mer=_f(parts, 7),
        decoder_timecode=timestamp,
        raw=msg,
    )
