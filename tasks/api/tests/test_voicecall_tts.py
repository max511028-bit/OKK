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


class TestTrimEdgesPcm:
    """Обрезка тишины по краям (пункт 1 доработок 2026-07). Проверяем на
    синтетическом PCM (тишина+тон+тишина, без сети): края уходят, тон в
    середине остаётся, защита от съедания всей фразы работает."""

    @staticmethod
    def _pcm_silence(sec):
        return b"\x00\x00" * int(sec * 8000)

    @staticmethod
    def _pcm_tone(sec, amp=8000):
        import math, struct
        n = int(sec * 8000)
        return b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * 440 * i / 8000)))
                        for i in range(n))

    def test_trims_leading_and_trailing_silence(self):
        pcm = self._pcm_silence(1.0) + self._pcm_tone(1.0) + self._pcm_silence(1.0)
        out = tts._trim_edges_pcm(pcm)
        dur_in = len(pcm) / 2 / 8000
        dur_out = len(out) / 2 / 8000
        assert dur_in > 2.9
        # края (2с тишины) должны уйти, ~1с тона остаться (± допуск ffmpeg)
        assert 0.7 < dur_out < 1.6, f"ожидали ~1с, получили {dur_out:.2f}с"

    def test_empty_input_returns_empty(self):
        assert tts._trim_edges_pcm(b"") == b""

    def test_pure_silence_returns_original(self):
        """Защита: если вся фраза ушла бы под порог (чистая тишина/очень
        тихий голос) — возвращаем исходник, а не пустоту."""
        pcm = self._pcm_silence(1.0)
        out = tts._trim_edges_pcm(pcm)
        assert out == pcm

    def test_cache_key_has_trim_version(self):
        """Ключ кэша должен содержать версию обрезки — иначе старые
        необрезанные файлы кэша продолжали бы отдаваться."""
        # два вызова с одинаковыми аргументами стабильны
        assert tts._cache_key("текст", "v") == tts._cache_key("текст", "v")
        # а сам факт наличия trim-версии проверяем косвенно: смена
        # реализации ключа обязана менять хэш (защита от «забыли
        # инвалидировать»)
        import hashlib
        expected = hashlib.sha1("v||+0%||trim1||текст".encode("utf-8")).hexdigest()[:16]
        assert tts._cache_key("текст", "v") == expected
