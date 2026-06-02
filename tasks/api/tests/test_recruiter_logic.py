"""Тесты для recruiter_logic — детектор возражений, вырезка ТЗ, промпты."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import recruiter_logic as rl


class TestDetectObjectionType:
    def test_salary(self):
        assert "salary" in rl._detect_objection_type("Мало платят, хочу больше зарплаты")

    def test_transport(self):
        assert "transport" in rl._detect_objection_type("Далеко добираться, нет автобуса")

    def test_schedule(self):
        assert "schedule" in rl._detect_objection_type("Не нравится график смен ночных")

    def test_contract(self):
        assert "contract" in rl._detect_objection_type("Не хочу ГПХ, нужен трудовой договор")

    def test_rotation(self):
        assert "rotation" in rl._detect_objection_type("Не пойду на вахту, без общежития")

    def test_experience(self):
        assert "experience" in rl._detect_objection_type("Я новичок, нет опыта")

    def test_empty_falls_back(self):
        types = rl._detect_objection_type("просто чо?")
        assert "salary" in types and "general" in types

    def test_general_always_present(self):
        types = rl._detect_objection_type("мало платят")
        assert "general" in types

    def test_combined_objection_returns_multiple(self):
        types = rl._detect_objection_type("мало платят и далеко добираться")
        assert "salary" in types and "transport" in types

    def test_ranking_by_score(self):
        # больше слов про график → schedule должен быть первым
        types = rl._detect_objection_type("график смен ночной режим сутки часов")
        assert types[0] == "schedule"


class TestExtractRelevantTz:
    def test_small_tz_returned_as_is(self):
        tz = "короткое ТЗ\nстрока 2"
        assert rl.extract_relevant_tz(tz, "мало платят") == tz

    def test_large_tz_truncated(self):
        # Сделать большое ТЗ и убедиться что результат меньше MAX_TZ_CHARS (+небольшой суффикс)
        big = "=== Лист: Главное\n" + "\n".join(f"строка {i} зарплата вахта график" for i in range(1000))
        result = rl.extract_relevant_tz(big, "мало платят")
        assert len(result) <= rl.MAX_TZ_CHARS + 100

    def test_relevant_lines_kept(self):
        big = "=== Лист: a\n" + "ZZZ нерелевантное\n" * 500 + "оплата 150 руб в час\n" + "ZZZ нерелевантное\n" * 500
        # Сначала убедимся что ТЗ действительно большое
        assert len(big) > rl.MAX_TZ_CHARS
        result = rl.extract_relevant_tz(big, "мало платят зарплат оплат")
        assert "оплата 150" in result


class TestBuildPrompt:
    def test_contains_required_sections(self):
        p = rl.build_prompt("ТЗ текст", "учебник текст", "возражение")
        assert "ТЗ ПРОЕКТА" in p
        assert "УЧЕБНИК" in p
        assert "ВОЗРАЖЕНИЕ КАНДИДАТА" in p
        assert "возражение" in p

    def test_uses_custom_system_prompt_when_provided(self):
        p = rl.build_prompt("tz", "hb", "obj", system_prompt="МОЙ КАСТОМНЫЙ СИСТЕМНЫЙ")
        assert "МОЙ КАСТОМНЫЙ СИСТЕМНЫЙ" in p

    def test_uses_default_when_blank_system(self):
        p = rl.build_prompt("tz", "hb", "obj", system_prompt="   ")
        assert rl.RECRUITER_SYSTEM_PROMPT.strip()[:30] in p


class TestBuildChatPrompt:
    def test_chat_prompt_includes_history(self):
        history = [
            {"role": "user", "content": "первый вопрос"},
            {"role": "assistant", "content": "первый ответ"},
        ]
        p = rl.build_chat_prompt(
            tz="tz", handbook="hb", objection="мало платят",
            first_answer="вот скрипт",
            chat_history=history, user_message="уточняющий вопрос",
        )
        assert "уточняющий вопрос" in p


class TestTzCache:
    def test_cache_module_level_dict(self):
        # Кэш просто dict — проверяем что он есть и куда-то пишется/читается без падения
        assert isinstance(rl._tz_cache, dict)
        rl._tz_cache["__test__"] = ("payload", 0)
        assert rl._tz_cache["__test__"] == ("payload", 0)
        del rl._tz_cache["__test__"]
