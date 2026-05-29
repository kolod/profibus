from __future__ import annotations

import time
from dataclasses import dataclass, field

from pyprofibus.dp import DpTelegram_SlaveDiag_Con, DpTelegram_SlaveDiag_Req
from pyprofibus.fdl import FdlTelegram, FdlTelegram_FdlStat_Req
from pyprofibus.phy_serial import CpPhySerial
from pyprofibus.util import bytesToHex

from profibus_debug.bus import open_phy

# FDL address extension bit — set in DA/SA when SAPs are present
_ADDR_EXT = 0x80
_ADDR_MASK = 0x7F


@dataclass
class SlaveDiagnostics:
    addr: int
    ident_number: int
    master_addr: int

    # Station status byte 0
    station_non_existent: bool = False
    station_not_ready: bool = False
    cfg_fault: bool = False
    ext_diag: bool = False
    not_supported: bool = False
    invalid_slave_response: bool = False
    prm_fault: bool = False
    master_lock: bool = False

    # Station status byte 1
    prm_req: bool = False
    stat_diag: bool = False
    watchdog_on: bool = False
    freeze_mode: bool = False
    sync_mode: bool = False
    deactivated: bool = False

    # Station status byte 2
    ext_diag_overflow: bool = False

    # Extended diagnostic bytes (device-specific, beyond the 6-byte header)
    ext_diag_data: bytes = field(default_factory=bytes)

    @property
    def ready_for_data_exchange(self) -> bool:
        return not any([
            self.station_non_existent,
            self.station_not_ready,
            self.cfg_fault,
            self.prm_fault,
            self.prm_req,
        ])


def _drain_phy(phy: CpPhySerial, settle: float = 0.03) -> None:
    """Flush the receive buffer by polling until silent for `settle` seconds."""
    deadline = time.monotonic() + settle
    while time.monotonic() < deadline:
        data = phy.pollData(min(0.005, deadline - time.monotonic()))
        if data is not None:
            # Received something — reset the settle window
            deadline = time.monotonic() + settle


def _warmup(phy: CpPhySerial, addr: int, master_addr: int,
            probes: int, interval: float, debug: bool) -> int:
    """Send FdlStat probes until the slave responds, to trigger CBP2 baud-rate lock.

    Returns the number of responses received.
    """
    raw_stat = FdlTelegram_FdlStat_Req(da=addr, sa=master_addr).getRawData()
    responses = 0
    for i in range(probes):
        _drain_phy(phy, settle=0.01)
        phy.sendData(raw_stat, srd=True)
        deadline = time.monotonic() + interval
        while time.monotonic() < deadline:
            raw = phy.pollData(min(0.01, deadline - time.monotonic()))
            if raw is None:
                continue
            try:
                fdl = FdlTelegram.fromRawData(raw)
            except Exception:
                continue
            sa = (getattr(fdl, "sa", None) or 0) & _ADDR_MASK
            if sa == addr:
                responses += 1
                if debug:
                    print(f"PHY-serial: warm-up response {responses} on probe {i + 1}/{probes}")
                break
    return responses


def read_diagnostics(
    port: str,
    addr: int,
    baudrate: int = 9600,
    master_addr: int = 0,
    timeout: float = 0.5,
    retries: int = 3,
    warmup_probes: int = 10,
    warmup_interval: float = 0.1,
    debug: bool = False,
) -> SlaveDiagnostics | None:
    """Request DP slave diagnostics from a single slave address.

    Sends FdlStat warm-up probes first so devices like the Siemens CBP2
    board can complete baud-rate auto-detection before the SlaveDiag
    request is sent.

    Returns None if the slave does not respond within the timeout.
    """
    phy = open_phy(port, baudrate, debug)
    try:
        if warmup_probes > 0:
            responses = _warmup(phy, addr, master_addr,
                                warmup_probes, warmup_interval, debug)
            if debug:
                print(f"PHY-serial: warm-up complete — {responses}/{warmup_probes} responses")

        req = DpTelegram_SlaveDiag_Req(da=addr, sa=master_addr)
        raw_req = req.toFdlTelegram().getRawData()

        for attempt in range(retries):
            _drain_phy(phy)
            phy.sendData(raw_req, srd=True)

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                raw = phy.pollData(min(0.02, remaining))
                if raw is None:
                    continue

                if debug:
                    print(f"PHY-serial: RX (attempt {attempt + 1})  {bytesToHex(raw)}")

                try:
                    fdl = FdlTelegram.fromRawData(raw)
                except Exception as exc:
                    if debug:
                        print(f"PHY-serial: parse error: {exc}")
                    continue

                # DA/SA carry the address extension bit when SAPs are used —
                # mask it off before comparing plain addresses.
                sa = (getattr(fdl, "sa", None) or 0) & _ADDR_MASK
                da = (getattr(fdl, "da", None) or 0) & _ADDR_MASK
                if sa != addr or da != master_addr:
                    continue

                du = getattr(fdl, "du", None)
                if du is None or len(du) < 6:
                    continue

                con = DpTelegram_SlaveDiag_Con.fromFdlTelegram(fdl)
                return SlaveDiagnostics(
                    addr=addr,
                    ident_number=con.identNumber,
                    master_addr=con.masterAddr,
                    station_non_existent=con.notExist(),
                    station_not_ready=con.notReady(),
                    cfg_fault=con.cfgFault(),
                    ext_diag=con.hasExtDiag(),
                    not_supported=con.isNotSupp(),
                    invalid_slave_response=bool(con.b0 & DpTelegram_SlaveDiag_Con.B0_INVALSR),
                    prm_fault=con.prmFault(),
                    master_lock=con.masterLock(),
                    prm_req=con.prmReq(),
                    stat_diag=bool(con.b1 & DpTelegram_SlaveDiag_Con.B1_SDIAG),
                    watchdog_on=bool(con.b1 & DpTelegram_SlaveDiag_Con.B1_WD),
                    freeze_mode=bool(con.b1 & DpTelegram_SlaveDiag_Con.B1_FREEZE),
                    sync_mode=bool(con.b1 & DpTelegram_SlaveDiag_Con.B1_SYNC),
                    deactivated=bool(con.b1 & DpTelegram_SlaveDiag_Con.B1_DEAC),
                    ext_diag_overflow=bool(con.b2 & DpTelegram_SlaveDiag_Con.B2_EXTDIAGOVR),
                    ext_diag_data=bytes(du[6:]),
                )
    finally:
        phy.close()

    return None
