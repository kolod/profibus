from __future__ import annotations

from json import dumps, loads
from pathlib import Path

_CONFIG_PATH = Path.home() / ".profibus-debug" / "session.json"


def load_last_hwid() -> str | None:
    try:
        return loads(_CONFIG_PATH.read_text())["hwid"]
    except Exception:
        return None


def save_last_hwid(hwid: str) -> None:
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(dumps({"hwid": hwid}))
    except Exception:
        pass
