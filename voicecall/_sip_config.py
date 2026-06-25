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
    """Локальный IP в LAN — нужен pyVoIP'у для RTP. 0.0.0.0 не годится за NAT.
    Игнорируем docker/WSL bridge'ы (172.x), берём только обычный LAN (192.168.x / 10.x)."""
    # 1) Стандартный трюк — какой IP роутится наружу
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    candidate = None
    try:
        s.connect(("8.8.8.8", 80))
        candidate = s.getsockname()[0]
    except Exception:
        pass
    finally:
        s.close()

    # Если попался docker/WSL bridge — ищем настоящий LAN IP через hostname
    if candidate and candidate.startswith(("172.17.", "172.18.", "172.19.", "172.20.")):
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if ip.startswith(("192.168.", "10.")) and not ip.startswith("127."):
                    return ip
        except Exception:
            pass
    return candidate or "127.0.0.1"


def require(env: dict, key: str) -> str:
    v = env.get(key, "").strip()
    if not v or v.startswith("ЗАМЕНИ"):
        print(f"❌ В voicecall.env не задано {key}.", file=sys.stderr)
        sys.exit(1)
    return v
