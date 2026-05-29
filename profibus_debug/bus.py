from __future__ import annotations

import time
from collections.abc import Callable
from typing import Iterator

from pyprofibus.fdl import FdlTelegram, FdlTelegram_FdlStat_Req
from pyprofibus.phy_serial import CpPhySerial


def open_phy(port: str, baudrate: int = 9600, debug: bool = False) -> CpPhySerial:
    phy = CpPhySerial(port=port, debug=debug)
    phy.setConfig(baudrate=baudrate)
    return phy


def _drain(phy: CpPhySerial) -> None:
    """Discard all bytes currently sitting in the receive buffer."""
    while phy.pollData(0.0) is not None:
        pass


def discover_devices(
    port: str,
    baudrate: int = 9600,
    master_addr: int = 0,
    timeout_per_addr: float = 0.05,
    debug: bool = False,
    on_probe: Callable[[int], None] | None = None,
) -> Iterator[int]:
    """Yield addresses of responding PROFIBUS DP slaves (0–125).

    Uses sendData/pollData directly to bypass CpPhy's bus-allocation queue,
    which would otherwise throttle sends to one every ~300 ms at 9600 baud.
    """
    phy = open_phy(port, baudrate, debug)
    try:
        for addr in range(0, 126):
            if on_probe:
                on_probe(addr)
            _drain(phy)

            req = FdlTelegram_FdlStat_Req(da=addr, sa=master_addr)
            phy.sendData(req.getRawData(), srd=True)

            deadline = time.monotonic() + timeout_per_addr
            found = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                raw = phy.pollData(min(0.01, remaining))
                if raw is None:
                    continue
                try:
                    telegram = FdlTelegram.fromRawData(raw)
                except Exception:
                    continue
                if getattr(telegram, "sa", None) == addr:
                    found = True
                    break

            if found:
                _drain(phy)
                yield addr
    finally:
        phy.close()
