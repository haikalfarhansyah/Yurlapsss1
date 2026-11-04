"""Simulator decoder untuk TES saja (bukan bagian dari aplikasi).

YurLaps tidak punya mode demo di dalam aplikasi; file ini hanya dipakai
``tests/`` untuk menguji pipa ZeroMQ tanpa hardware.
"""
from __future__ import annotations

import threading
import time


class SimulatedDecoder(threading.Thread):
    """Publish pesan OpenStint sintetis ke alamat ZeroMQ (khusus uji)."""

    def __init__(self, endpoint: str, transponders=(1615544, 3616557, 4021888),
                 lap_seconds=(9.4, 10.8, 12.3), speed: float = 1.0):
        super().__init__(name="decoder-sim", daemon=True)
        self.endpoint = endpoint
        self.transponders = list(transponders)
        self.lap_seconds = list(lap_seconds)
        self.speed = speed
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            import random
            import zmq  # type: ignore
        except ImportError:
            return

        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.PUB)
        sock.setsockopt(zmq.LINGER, 0)
        port = self.endpoint.rsplit(":", 1)[-1]
        try:
            sock.bind(self.endpoint.replace("127.0.0.1", "*"))
        except Exception:
            try:
                sock.connect(f"tcp://127.0.0.1:{port}")
            except Exception:
                return

        start = time.monotonic()
        next_lap = {t: 2.0 + 3 * i for i, t in enumerate(self.transponders)}
        next_status = 0.0
        while not self._stop.is_set():
            now = (time.monotonic() - start) * self.speed
            if now >= next_status:
                next_status = now + 1.0
                noise = -41.0 + random.uniform(-2.5, 2.5)
                recv = random.randint(120, 480)
                sock.send_string(
                    f"S {int(now*1000)} {noise:.3f} {5.0 + random.uniform(-.6,.6):.2f} "
                    f"{recv} {int(recv*random.uniform(.72,.96))} {random.uniform(1.2,3.4):.2f}")
            for i, tid in enumerate(self.transponders):
                if now >= next_lap[tid]:
                    base = self.lap_seconds[i % len(self.lap_seconds)]
                    next_lap[tid] = now + base + random.uniform(-0.45, 0.6)
                    typ = "OPN" if i % 2 == 0 else "AMB"
                    sock.send_string(
                        f"P {int(now*1000)} {typ} {tid} {random.uniform(-4.2,-2.4):.2f} "
                        f"{random.randint(40,80)} {random.randint(82000,102000)} "
                        f"{random.uniform(6.5,14.0):.1f}")
            time.sleep(0.02)
        sock.close()
