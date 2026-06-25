"""Загрузка SIP-креденшалов из C:\\ProgramData\\sth\\voicecall.env.
Файл не лежит в git, секреты не утекают.
"""
import os
import socket
import sys
from pathlib import Path

ENV_PATH = Path(r"C:\ProgramData\sth\voicecall.env")


def load_env() -> dict:
    if not ENV_PATH.exists():
        print(f"❌ Файл {ENV_PATH} не найден.", file=sys.stderr)
        print("   Создай его (или запусти voicecall/install_windows.ps1).", file=sys.stderr)
        sys.exit(1)
    out: dict = {}
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def get_local_ip() -> str:
    """Локальный IP в LAN — нужен pyVoIP'у для RTP. 0.0.0.0 не годится за NAT."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def require(env: dict, key: str) -> str:
    v = env.get(key, "").strip()
    if not v or v.startswith("ЗАМЕНИ"):
        print(f"❌ В voicecall.env не задано {key}.", file=sys.stderr)
        sys.exit(1)
    return v
