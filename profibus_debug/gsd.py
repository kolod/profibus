from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pyprofibus.dp import DpCfgDataElement
from pyprofibus.gsd.parser import GsdParser


@dataclass
class GsdModule:
    name: str
    cfg_bytes: bytes
    input_bytes: int    # bytes slave sends to master (position, status)
    output_bytes: int   # bytes master sends to slave (preset, control)
    user_prm_data: bytes


@dataclass
class GsdDevice:
    vendor_name: str
    model_name: str
    ident_number: int
    modules: list[GsdModule] = field(default_factory=list)


def _decode_cfg_sizes(cfg_bytes: bytes) -> tuple[int, int]:
    in_bytes = 0
    out_bytes = 0
    for b in cfg_bytes:
        id_type = b & DpCfgDataElement.ID_TYPE_MASK
        if id_type == DpCfgDataElement.ID_TYPE_SPEC:
            continue
        item_size = 2 if (b & DpCfgDataElement.ID_LEN_WORDS) else 1
        count = (b & DpCfgDataElement.ID_LEN_MASK) + 1
        total = item_size * count
        if id_type == DpCfgDataElement.ID_TYPE_IN:
            in_bytes += total
        elif id_type == DpCfgDataElement.ID_TYPE_OUT:
            out_bytes += total
        elif id_type == DpCfgDataElement.ID_TYPE_INOUT:
            in_bytes += total
            out_bytes += total
    return in_bytes, out_bytes


def _build_module_prm_data(module_fields: dict) -> bytes:
    prm_len = module_fields.get("Ext_Module_Prm_Data_Len", 0)
    if not prm_len:
        return b""
    buf = bytearray(prm_len)
    for const in module_fields.get("Ext_User_Prm_Data_Const", []):
        for i, val in enumerate(const.dataBytes):
            pos = const.offset + i
            if pos < prm_len:
                buf[pos] = val
    return bytes(buf)


def parse_gsd(path: Path | str) -> GsdDevice:
    gsd = GsdParser.fromFile(str(path))
    modules = []
    for m in (gsd.getField("Module") or []):
        in_b, out_b = _decode_cfg_sizes(m.configBytes)
        modules.append(GsdModule(
            name=m.name,
            cfg_bytes=m.configBytes,
            input_bytes=in_b,
            output_bytes=out_b,
            user_prm_data=_build_module_prm_data(m.fields),
        ))
    return GsdDevice(
        vendor_name=gsd.getField("Vendor_Name", ""),
        model_name=gsd.getField("Model_Name", ""),
        ident_number=gsd.getField("Ident_Number", 0),
        modules=modules,
    )
