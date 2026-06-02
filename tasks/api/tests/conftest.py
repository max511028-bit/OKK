"""Общие фикстуры для тестов tasks-api.

Подменяем TASKS_DB через env ДО импорта main, чтобы main подхватил временную БД.
Токены берём через _make_token — он HMAC-выводит их из auth_secret.bin модуля.
"""
import os
import sys
import tempfile
import importlib
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[1]  # tasks/api
sys.path.insert(0, str(API_DIR))


@pytest.fixture(scope="function")
def tmp_db():
    """Создаёт временную SQLite, ставит TASKS_DB env, чистит после теста."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    prev = os.environ.get("TASKS_DB")
    os.environ["TASKS_DB"] = path
    # Сбросить module cache main, чтобы он перечитал env
    for mod in list(sys.modules.keys()):
        if mod == "main" or mod.startswith("main."):
            del sys.modules[mod]
    yield path
    if prev is None:
        os.environ.pop("TASKS_DB", None)
    else:
        os.environ["TASKS_DB"] = prev
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture(scope="function")
def main_module(tmp_db):
    """Свежеимпортированный main с временной БД и проинициализированной схемой."""
    import main as _main
    importlib.reload(_main)
    _main.DB_PATH = tmp_db
    _main.init_db()
    return _main


@pytest.fixture(scope="function")
def client(main_module):
    """FastAPI TestClient на временной БД."""
    from fastapi.testclient import TestClient
    return TestClient(main_module.app)


@pytest.fixture(scope="function")
def admin_token(main_module):
    """Реальный admin-токен, выведенный HMAC из auth_secret.bin."""
    return main_module._make_token("admin")


@pytest.fixture(scope="function")
def portal_token(main_module):
    """Реальный portal-токен."""
    return main_module._make_token("portal")
