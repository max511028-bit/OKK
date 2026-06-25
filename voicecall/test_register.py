"""Тест регистрации SIP-аккаунта в Novofon.
Не требует баланса — только проверяет что креденшалы валидны
и сеть пропускает SIP-трафик.

Запуск:
    python voicecall/test_register.py
"""
import sys
import time

# Чиним вывод эмодзи/кириллицы на Windows (Python 3.13+ всё ещё может падать в cp1251)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from _sip_config import get_local_ip, load_env, require

try:
    # pyVoIP экспортирует по-разному в разных версиях — пробуем оба пути
    try:
        from pyVoIP.VoIP import VoIPPhone  # type: ignore
    except ImportError:
        from pyVoIP.VoIP.phone import VoIPPhone  # type: ignore
except ImportError as e:
    print("❌ pyVoIP не установлена.", file=sys.stderr)
    print("   Запусти: powershell -ExecutionPolicy Bypass -File voicecall\\install_windows.ps1", file=sys.stderr)
    print(f"   Детали: {e}", file=sys.stderr)
    sys.exit(1)


def main():
    env = load_env()
    server = require(env, "SIP_SERVER")
    port = int(env.get("SIP_PORT", "5060"))
    user = require(env, "SIP_USER")
    pwd = require(env, "SIP_PASS")
    local_ip = get_local_ip()

    print(f"=== SIP registration test ===")
    print(f"Server:    {server}:{port}")
    print(f"User:      {user}")
    print(f"Local IP:  {local_ip}")
    print()

    phone = VoIPPhone(
        server=server,
        port=port,
        username=user,
        password=pwd,
        myIP=local_ip,
        callCallback=lambda call: None,  # пустой колбэк, входящих не ждём
    )

    try:
        print("Регистрируюсь на сервере...")
        phone.start()
    except Exception as e:
        print(f"❌ Не удалось зарегистрироваться: {type(e).__name__}: {e}", file=sys.stderr)
        print()
        print("Возможные причины:")
        print("  - Креденшалы неверные (логин/пароль из Novofon)")
        print("  - Аккаунт не активирован (документы ещё на проверке)")
        print("  - Сеть/файрвол блокирует SIP (UDP/5060)")
        print("  - У провайдера временные проблемы")
        sys.exit(2)

    print("✅ REGISTERED to", server)
    print()
    print("Подключение активно. Держу 20 секунд для теста стабильности...")
    try:
        for sec in range(20, 0, -1):
            print(f"  осталось {sec} сек...", end="\r")
            time.sleep(1)
        print()
    except KeyboardInterrupt:
        print("\nПрерывание пользователем.")
    finally:
        print("Останавливаю...")
        try:
            phone.stop()
        except Exception:
            pass
        print("✅ Тест регистрации завершён успешно. SIP работает.")


if __name__ == "__main__":
    main()
