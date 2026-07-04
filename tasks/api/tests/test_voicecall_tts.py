"""Юнит-тесты для voicecall/tts.py — только чистая логика (ключ кэша,
разбор rate), без реальных сетевых вызовов к edge-tts/Silero (недоступны
в CI и не нужны для этих проверок)."""
import sys
from pathlib import Path

VOICECALL_DIR = Path(__file__).resolve().parents[3] / "voicecall"
sys.path.insert(0, str(VOICECALL_DIR))

import tts  # noqa: E402


class TestCacheKeyIncludesVoiceAndRate:
    """Часть 2 доработок 2026-07: без rate/voice в ключе кэша разные
    настройки сценария схлопывались бы в один и тот же файл, и настройка
    скорости/голоса молча игнорировалась бы после первого прогона."""

    def test_different_rate_gives_different_key(self):
        k1 = tts._cache_key("привет", "silero:kseniya", "+0%")
        k2 = tts._cache_key("привет", "silero:kseniya", "+20%")
        assert k1 != k2

    def test_different_voice_gives_different_key(self):
        k1 = tts._cache_key("привет", "silero:kseniya", "+0%")
        k2 = tts._cache_key("привет", "silero:aidar", "+0%")
        assert k1 != k2

    def test_same_inputs_give_same_key(self):
        k1 = tts._cache_key("привет", "silero:kseniya", "+10%")
        k2 = tts._cache_key("привет", "silero:kseniya", "+10%")
        assert k1 == k2

    def test_default_rate_matches_explicit_zero(self):
        # _cache_key(text, voice) без rate должен совпадать с явным "+0%" —
        # иначе прогрев (без явного rate) и звонок (с rate="+0%" из
        # settings по умолчанию) промахнутся мимо друг друга по кэшу.
        assert tts._cache_key("текст", "v") == tts._cache_key("текст", "v", "+0%")


class TestRateToAtempo:
    def test_zero_rate_is_no_change(self):
        assert tts._rate_to_atempo("+0%") == 1.0
        assert tts._rate_to_atempo("") == 1.0
        assert tts._rate_to_atempo(None) == 1.0

    def test_positive_rate_speeds_up(self):
        assert tts._rate_to_atempo("+10%") == 1.10
        assert tts._rate_to_atempo("+20%") == 1.20

    def test_negative_rate_slows_down(self):
        assert tts._rate_to_atempo("-10%") == 0.90

    def test_clamped_to_ffmpeg_atempo_limits(self):
        # ffmpeg atempo поддерживает только 0.5-2.0 за один проход
        assert tts._rate_to_atempo("+500%") == 2.0
        assert tts._rate_to_atempo("-90%") == 0.5

    def test_garbage_rate_defaults_to_no_change(self):
        assert tts._rate_to_atempo("не число") == 1.0


class TestVoiceChoicesListedForUi:
    def test_voice_choices_cover_both_engines(self):
        ids = [v["id"] for v in tts.VOICE_CHOICES]
        assert "ru-RU-SvetlanaNeural" in ids
        assert "ru-RU-DmitryNeural" in ids
        silero_voices = [i for i in ids if i.startswith("silero:")]
        assert len(silero_voices) == 5  # kseniya, baya, xenia, aidar, eugene

    def test_every_choice_has_a_label(self):
        for v in tts.VOICE_CHOICES:
            assert v.get("label")


class TestPrewarmExtraTexts:
    def test_extra_texts_included_without_scenario_steps(self, tmp_path, monkeypatch):
        """prewarm_scenario(extra_texts=...) — филлеры прогреваются даже
        если сценарий сам по себе не даёт новых текстов (используется для
        dialog.FILLER_PHRASES, не входящих в steps/closing)."""
        calls = []
        monkeypatch.setattr(tts, "CACHE_DIR", tmp_path)
        monkeypatch.setattr(tts, "synthesize_telephony_pcm",
                             lambda t, voice, use_cache=True, rate="+0%": calls.append((t, voice, rate)) or b"x")
        n = tts.prewarm_scenario({"steps": [], "closing": ""}, voice="silero:kseniya",
                                  rate="+5%", verbose=False, extra_texts=["Ага.", "Угу."])
        assert n == 2
        assert {c[0] for c in calls} == {"Ага.", "Угу."}
        assert all(c[1] == "silero:kseniya" and c[2] == "+5%" for c in calls)
