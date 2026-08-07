"""Повторы обращения к LLM (04.08). Разбор кампании «СТХ НДЗ 03.08»:
у контакта 766 вердикт «годен» остался непроверенным — робо-проверка не
отработала. В логе три сорванных обращения из одиннадцати: два
`502 Bad Gateway` и таймаут. Сама Ollama здорова (замер: 0.1с на ответ),
сбоит туннель VPS↔ПК, а повтора в коде не было вовсе.

Сеть не задействована: _rpc подменяется.
"""
import sys
import urllib.error
from pathlib import Path

import pytest

VOICECALL_DIR = Path(__file__).resolve().parents[3] / "voicecall"
sys.path.insert(0, str(VOICECALL_DIR))

import dispatch_agent as da  # noqa: E402

# Соседние наборы тестов подменяют da._llm_ask присваиванием и НЕ
# возвращают его обратно — модуль-то общий на весь прогон. По одному
# файлу тесты зеленели, а в общем прогоне падали все одиннадцать.
# Ловим настоящую функцию на импорте (до запуска тестов) и возвращаем
# перед каждым своим.
_REAL_LLM_ASK = da._llm_ask


@pytest.fixture(autouse=True)
def _restore_real_llm_ask():
    da._llm_ask = _REAL_LLM_ASK
    yield
    da._llm_ask = _REAL_LLM_ASK


def _no_sleep(monkeypatch):
    """Тесты не должны ждать реальные паузы между попытками."""
    monkeypatch.setattr(da.time, "sleep", lambda *_: None)


def _answers(monkeypatch, seq):
    """Подменяет _rpc: элемент-исключение бросается, словарь возвращается."""
    calls = []

    def fake(*a, **kw):
        item = seq[min(len(calls), len(seq) - 1)]
        calls.append(kw.get("json_body"))
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr(da, "_rpc", fake)
    return calls


def _ok(text="человек"):
    return {"message": {"content": text}}


class TestRetryOnTransient:
    def test_502_retried_and_succeeds(self, monkeypatch):
        """Ровно тот случай, что был у Валентины."""
        _no_sleep(monkeypatch)
        calls = _answers(monkeypatch, [
            urllib.error.HTTPError("u", 502, "Bad Gateway", {}, None), _ok("робот")])
        assert da._llm_ask("x", "prompt") == "робот"
        assert len(calls) == 2, "должна была быть вторая попытка"

    def test_timeout_retried(self, monkeypatch):
        _no_sleep(monkeypatch)
        calls = _answers(monkeypatch, [TimeoutError("read timed out"), _ok("человек")])
        assert da._llm_ask("x", "prompt") == "человек"
        assert len(calls) == 2

    def test_gives_up_after_all_attempts(self, monkeypatch):
        _no_sleep(monkeypatch)
        calls = _answers(monkeypatch, [
            urllib.error.HTTPError("u", 503, "unavailable", {}, None)])
        assert da._llm_ask("x", "prompt", attempts=3) == ""
        assert len(calls) == 3

    def test_success_on_first_try_makes_one_call(self, monkeypatch):
        _no_sleep(monkeypatch)
        calls = _answers(monkeypatch, [_ok("человек")])
        assert da._llm_ask("x", "prompt") == "человек"
        assert len(calls) == 1, "лишних запросов быть не должно"


class TestNoRetryWhenPointless:
    """4xx повтором не лечится — запрос не станет правильнее."""

    def test_400_not_retried(self, monkeypatch):
        _no_sleep(monkeypatch)
        calls = _answers(monkeypatch, [
            urllib.error.HTTPError("u", 400, "bad request", {}, None)])
        assert da._llm_ask("x", "prompt") == ""
        assert len(calls) == 1

    def test_403_not_retried(self, monkeypatch):
        _no_sleep(monkeypatch)
        calls = _answers(monkeypatch, [
            urllib.error.HTTPError("u", 403, "forbidden", {}, None)])
        assert da._llm_ask("x", "prompt") == ""
        assert len(calls) == 1

    def test_429_is_retried(self, monkeypatch):
        """А вот «слишком часто» — временное, повторяем."""
        _no_sleep(monkeypatch)
        calls = _answers(monkeypatch, [
            urllib.error.HTTPError("u", 429, "too many", {}, None), _ok("человек")])
        assert da._llm_ask("x", "prompt") == "человек"
        assert len(calls) == 2


class TestVotingKeepsRequestsBounded:
    """Голосование само по себе запас, поэтому на голос повторов меньше:
    иначе 3 голоса × 3 попытки = 9 запросов на один контакт, а Ollama на
    той же машине обслуживает живые звонки."""

    def test_single_vote_uses_full_attempts(self, monkeypatch):
        _no_sleep(monkeypatch)
        calls = _answers(monkeypatch, [
            urllib.error.HTTPError("u", 502, "bad", {}, None)])
        long = "а расскажите побольше кто это откуда у вас информация я подумал"
        da._llm_robot_verdict("x", long, votes=1)
        assert len(calls) == 3

    def test_voting_caps_attempts_per_vote(self, monkeypatch):
        _no_sleep(monkeypatch)
        calls = _answers(monkeypatch, [
            urllib.error.HTTPError("u", 502, "bad", {}, None)])
        long = "а расскажите побольше кто это откуда у вас информация я подумал"
        da._llm_robot_verdict("x", long, votes=3)
        assert len(calls) == 6, f"ждём 3 голоса по 2 попытки, а вышло {len(calls)}"

    def test_failed_check_still_reports_not_checked(self, monkeypatch):
        """Главное свойство сохраняется: не смогли спросить — так и говорим,
        а не выдаём «человек»."""
        _no_sleep(monkeypatch)
        _answers(monkeypatch, [urllib.error.HTTPError("u", 502, "bad", {}, None)])
        long = "а расскажите побольше кто это откуда у вас информация я подумал"
        is_robot, checked = da._llm_robot_verdict("x", long, votes=3)
        assert is_robot is False and checked is False


class TestLiveCallUnaffected:
    """Паузы между попытками не должны попасть в живой разговор."""

    def test_llm_ask_absent_from_call_path(self):
        call_path = (VOICECALL_DIR / "phone_call.py").read_text(encoding="utf-8")
        dialog_src = (VOICECALL_DIR / "dialog.py").read_text(encoding="utf-8")
        assert "_llm_ask" not in call_path
        assert "_llm_ask" not in dialog_src
