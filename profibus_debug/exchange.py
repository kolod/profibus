from __future__ import annotations

import time
from collections.abc import Callable

from pyprofibus.dp import (
    DpCfgDataElement,
    DpTelegram_ChkCfg_Req,
    DpTelegram_DataExchange_Req,
    DpTelegram_SetPrm_Req,
    DpTelegram_SlaveDiag_Con,
    DpTelegram_SlaveDiag_Req,
)
from pyprofibus.fdl import FdlFCB, FdlTelegram, FdlTelegram_ack, FdlTelegram_FdlStat_Req
from pyprofibus.phy_serial import CpPhySerial

from profibus_debug.bus import open_phy
from profibus_debug.diagnostics import _drain_phy, _warmup
from profibus_debug.gsd import GsdDevice, GsdModule

_ADDR_MASK = 0x7F


def _patch_fc(raw: bytearray, fcb: FdlFCB) -> None:
    fc = raw[6] & ~(FdlTelegram.FC_FCB | FdlTelegram.FC_FCV)
    if fcb.bitIsOn():
        fc |= FdlTelegram.FC_FCB
    if fcb.bitIsValid():
        fc |= FdlTelegram.FC_FCV
    raw[6] = fc
    raw[-2] = sum(raw[4:-2]) & 0xFF


def _send_wait_ack(
    phy: CpPhySerial,
    raw: bytes,
    slave_addr: int,
    timeout: float,
    debug: bool,
) -> bool:
    """Send telegram and wait for Short ACK or any valid response from slave."""
    _drain_phy(phy, settle=0.01)
    phy.sendData(raw, srd=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = phy.pollData(min(0.01, deadline - time.monotonic()))
        if data is None:
            continue
        try:
            fdl = FdlTelegram.fromRawData(data)
        except Exception:
            continue
        if FdlTelegram_ack.checkType(fdl):
            if debug:
                print("PHY-serial: Short ACK received")
            return True
        if (getattr(fdl, "sa", None) or 0) & _ADDR_MASK == slave_addr:
            if debug:
                print(f"PHY-serial: Response from slave {slave_addr}")
            return True
    return False


def _make_set_prm(addr: int, master_addr: int, ident: int,
                  user_prm: bytes, fcb: FdlFCB) -> bytes:
    req = DpTelegram_SetPrm_Req(da=addr, sa=master_addr)
    req.identNumber = ident
    if user_prm:
        req.clearUserPrmData()
        req.addUserPrmData(user_prm)
    raw = bytearray(req.toFdlTelegram().getRawData())
    _patch_fc(raw, fcb)
    return bytes(raw)


def _make_chk_cfg(addr: int, master_addr: int, cfg_bytes: bytes, fcb: FdlFCB) -> bytes:
    req = DpTelegram_ChkCfg_Req(da=addr, sa=master_addr)
    for b in cfg_bytes:
        req.addCfgDataElement(DpCfgDataElement(identifier=b))
    raw = bytearray(req.toFdlTelegram().getRawData())
    _patch_fc(raw, fcb)
    return bytes(raw)


def _make_diag(addr: int, master_addr: int, fcb: FdlFCB) -> bytes:
    req = DpTelegram_SlaveDiag_Req(da=addr, sa=master_addr)
    raw = bytearray(req.toFdlTelegram().getRawData())
    _patch_fc(raw, fcb)
    return bytes(raw)


def _make_dx(addr: int, master_addr: int, out_data: bytes, fcb: FdlFCB) -> bytes:
    req = DpTelegram_DataExchange_Req(da=addr, sa=master_addr, du=out_data)
    raw = bytearray(req.toFdlTelegram().getRawData())
    _patch_fc(raw, fcb)
    return bytes(raw)


def _bus_alloc_send(phy: CpPhySerial, raw_stat: bytes, alloc_duration: float) -> None:
    """Prepend an FdlStat + alloc-wait before sending the next telegram.

    Mirrors CpPhy.send(maxReplyLen=255): the bus must stay quiet for the
    allocation window so that CBP2-style devices accept the following request.
    """
    _drain_phy(phy, settle=0.01)
    t0 = time.monotonic()
    phy.sendData(raw_stat, srd=True)
    deadline = t0 + 0.15
    while time.monotonic() < deadline:
        if phy.pollData(min(0.01, deadline - time.monotonic())) is not None:
            break
    wait = (t0 + alloc_duration) - time.monotonic()
    if wait > 0:
        time.sleep(wait)


def exchange_data(
    port: str,
    addr: int,
    device: GsdDevice,
    module: GsdModule,
    baudrate: int = 9600,
    master_addr: int = 0,
    timeout: float = 0.5,
    warmup_probes: int = 5,
    warmup_interval: float = 0.1,
    count: int = 1,
    interval: float = 0.2,
    debug: bool = False,
    on_cycle: Callable[[int, bytes], None] | None = None,
) -> list[bytes]:
    """Run DP startup (SetPrm + ChkCfg) then cyclic DataExchange.

    Returns list of received input-data frames (one per cycle).
    on_cycle(cycle_index, data) is called after each successful cycle.
    """
    phy = open_phy(port, baudrate, debug)
    fcb = FdlFCB(enable=True)
    results: list[bytes] = []

    _sec_per_byte = 11.0 / baudrate
    raw_stat = FdlTelegram_FdlStat_Req(da=addr, sa=master_addr).getRawData()
    _alloc = _sec_per_byte * (len(raw_stat) + 255)

    try:
        if warmup_probes > 0:
            resp = _warmup(phy, addr, master_addr, warmup_probes, warmup_interval, debug)
            if debug:
                print(f"PHY-serial: warm-up complete — {resp}/{warmup_probes} responses")

        # SetPrm
        _bus_alloc_send(phy, raw_stat, _alloc)
        raw_prm = _make_set_prm(addr, master_addr, device.ident_number,
                                 module.user_prm_data, fcb)
        if not _send_wait_ack(phy, raw_prm, addr, timeout, debug):
            raise RuntimeError(f"No ACK for SetPrm from slave {addr}")
        fcb.FCBnext()

        # ChkCfg
        _bus_alloc_send(phy, raw_stat, _alloc)
        raw_cfg = _make_chk_cfg(addr, master_addr, module.cfg_bytes, fcb)
        if not _send_wait_ack(phy, raw_cfg, addr, timeout, debug):
            raise RuntimeError(f"No ACK for ChkCfg from slave {addr}")
        fcb.FCBnext()

        # Poll SlaveDiag until ready (up to 10 s)
        ready = False
        ready_deadline = time.monotonic() + 10.0
        while time.monotonic() < ready_deadline:
            _bus_alloc_send(phy, raw_stat, _alloc)
            raw_diag = _make_diag(addr, master_addr, fcb)
            _drain_phy(phy, settle=0.01)
            phy.sendData(raw_diag, srd=True)
            diag_deadline = time.monotonic() + timeout
            while time.monotonic() < diag_deadline:
                raw = phy.pollData(min(0.01, diag_deadline - time.monotonic()))
                if raw is None:
                    continue
                try:
                    fdl = FdlTelegram.fromRawData(raw)
                    sa = (getattr(fdl, "sa", None) or 0) & _ADDR_MASK
                    if sa != addr:
                        continue
                    con = DpTelegram_SlaveDiag_Con.fromFdlTelegram(fdl)
                    if debug:
                        print(f"PHY-serial: SlaveDiag b0={con.b0:#04x} b1={con.b1:#04x}")
                    fcb.FCBnext()
                    if con.isReadyDataEx():
                        ready = True
                    elif con.cfgFault():
                        raise RuntimeError(f"Slave {addr}: cfg fault — wrong module selected")
                    elif con.prmFault():
                        raise RuntimeError(f"Slave {addr}: prm fault — wrong user parameters")
                except Exception as e:
                    if "fault" in str(e):
                        raise
                    continue
                break
            if ready:
                break
            time.sleep(0.2)

        if not ready:
            raise RuntimeError(f"Slave {addr} did not become ready for data exchange")

        # Cyclic DataExchange
        out_data = bytes(module.output_bytes)
        i = 0
        while count == 0 or i < count:
            _bus_alloc_send(phy, raw_stat, _alloc)
            raw_dx = _make_dx(addr, master_addr, out_data, fcb)
            _drain_phy(phy, settle=0.01)
            phy.sendData(raw_dx, srd=True)

            dx_deadline = time.monotonic() + timeout
            in_data: bytes | None = None
            while time.monotonic() < dx_deadline:
                raw = phy.pollData(min(0.01, dx_deadline - time.monotonic()))
                if raw is None:
                    continue
                try:
                    fdl = FdlTelegram.fromRawData(raw)
                except Exception:
                    continue
                if FdlTelegram_ack.checkType(fdl):
                    in_data = b""
                    break
                sa = (getattr(fdl, "sa", None) or 0) & _ADDR_MASK
                da = (getattr(fdl, "da", None) or 0) & _ADDR_MASK
                if sa == addr and da == master_addr:
                    du = getattr(fdl, "du", None)
                    if du is not None:
                        in_data = bytes(du)
                    break

            if in_data is not None:
                fcb.FCBnext()
                if len(in_data) == module.input_bytes:
                    results.append(in_data)
                    if on_cycle:
                        on_cycle(i, in_data)
                elif debug:
                    print(f"PHY-serial: DX got {len(in_data)} bytes, expected {module.input_bytes}")

            i += 1
            if count == 0 or i < count:
                time.sleep(interval)

    finally:
        phy.close()

    return results
