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


# Публичные STUN-серверы для определения нашего адреса:порта за NAT
# (проверено 2026-07-09: у пользователя cone-NAT — отображение порта не
# зависит от адресата, значит порт, который вернёт STUN, совпадёт с тем,
# на который Novofon будет слать RTP). Несколько на случай недоступности.
STUN_SERVERS = [
    ("stun.l.google.com", 19302),
    ("stun.cloudflare.com", 3478),
    ("stun.sipnet.ru", 3478),
]


def stun_query(sock: socket.socket, server_host: str, server_port: int,
               timeout: float = 3.0):
    """Шлёт STUN Binding Request С УКАЗАННОГО сокета и возвращает
    (public_ip, public_port) из XOR-MAPPED-ADDRESS — то есть как этот
    конкретный сокет виден снаружи через NAT. Важно слать именно с
    рабочего RTP-сокета pyVoIP: при cone-NAT отображение (public port)
    привязано к локальному порту, а STUN его и открывает/подтверждает.
    Любая ошибка → None (лучшее старание, не рушим звонок)."""
    import struct
    import os as _os
    tid = _os.urandom(12)
    req = struct.pack(">HHI", 0x0001, 0, 0x2112A442) + tid
    old_to = sock.gettimeout()
    old_blocking = sock.getblocking()
    try:
        sock.settimeout(timeout)
        sock.sendto(req, (socket.gethostbyname(server_host), server_port))
        for _ in range(5):  # можем получить чужой пакет — читаем до нашего ответа
            data, _addr = sock.recvfrom(2048)
            if len(data) < 20 or data[8:20] != tid:
                continue
            pos = 20
            while pos + 4 <= len(data):
                at, al = struct.unpack(">HH", data[pos:pos + 4])
                val = data[pos + 4:pos + 4 + al]
                if at == 0x0020 and al >= 8:  # XOR-MAPPED-ADDRESS
                    xport = struct.unpack(">H", val[2:4])[0] ^ 0x2112
                    ip = bytes(b ^ m for b, m in zip(val[4:8], b"\x21\x12\xa4\x42"))
                    return socket.inet_ntoa(ip), xport
                if at == 0x0001 and al >= 8:  # MAPPED-ADDRESS (без XOR)
                    port = struct.unpack(">H", val[2:4])[0]
                    return socket.inet_ntoa(val[4:8]), port
                pos += 4 + al + ((4 - al % 4) % 4)
            return None
    except Exception:
        return None
    finally:
        try:
            sock.settimeout(old_to)
            sock.setblocking(old_blocking)
        except Exception:
            pass
    return None


def stun_discover(sock: socket.socket, timeout: float = 3.0):
    """Пробегает по STUN_SERVERS, возвращает первое успешное
    (public_ip, public_port) для данного сокета, иначе None."""
    for host, port in STUN_SERVERS:
        r = stun_query(sock, host, port, timeout=timeout)
        if r:
            return r
    return None


def require(env: dict, key: str) -> str:
    v = env.get(key, "").strip()
    if not v or v.startswith("ЗАМЕНИ"):
        print(f"❌ В voicecall.env не задано {key}.", file=sys.stderr)
        sys.exit(1)
    return v
