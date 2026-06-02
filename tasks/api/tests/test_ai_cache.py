"""Тесты для 4.3 — кэш ответов ИИ."""


class TestCacheKey:
    def test_same_inputs_same_key(self, main_module):
        m = main_module
        msgs = [{"role": "system", "content": "ctx A=100"}]
        k1 = m._ai_cache_key("finance", "Топ-3 по марже?", msgs)
        k2 = m._ai_cache_key("finance", "Топ-3 по марже?", msgs)
        assert k1 == k2

    def test_different_context_different_key(self, main_module):
        m = main_module
        k1 = m._ai_cache_key("finance", "q", [{"role": "system", "content": "A=100"}])
        k2 = m._ai_cache_key("finance", "q", [{"role": "system", "content": "A=200"}])
        assert k1 != k2

    def test_different_question_different_key(self, main_module):
        m = main_module
        msgs = [{"role": "system", "content": "ctx"}]
        assert m._ai_cache_key("finance", "q1", msgs) != m._ai_cache_key("finance", "q2", msgs)

    def test_different_dashboard_different_key(self, main_module):
        m = main_module
        msgs = [{"role": "system", "content": "ctx"}]
        assert m._ai_cache_key("finance", "q", msgs) != m._ai_cache_key("wb", "q", msgs)

    def test_case_and_whitespace_normalization(self, main_module):
        m = main_module
        msgs = [{"role": "system", "content": "ctx"}]
        k1 = m._ai_cache_key("finance", "Топ-3 по марже?", msgs)
        k2 = m._ai_cache_key("finance", "  ТОП-3   ПО МАРЖЕ?  ", msgs)
        assert k1 == k2

    def test_user_messages_dont_affect_key(self, main_module):
        m = main_module
        sys_msg = {"role": "system", "content": "ctx"}
        k1 = m._ai_cache_key("finance", "q", [sys_msg, {"role": "user", "content": "X"}])
        k2 = m._ai_cache_key("finance", "q", [sys_msg, {"role": "user", "content": "Y"}])
        assert k1 == k2


class TestCacheGetPut:
    def test_put_then_get(self, main_module):
        m = main_module
        m._ai_cache_put("k1", "finance", "вопрос", "ответ модели")
        assert m._ai_cache_get("k1") == "ответ модели"

    def test_get_missing_returns_none(self, main_module):
        assert main_module._ai_cache_get("nonexistent-key") is None

    def test_put_empty_ignored(self, main_module):
        m = main_module
        m._ai_cache_put("k_empty", "finance", "q", "")
        m._ai_cache_put("k_blank", "finance", "q", "   \n  ")
        assert m._ai_cache_get("k_empty") is None
        assert m._ai_cache_get("k_blank") is None

    def test_hit_count_increments_on_get(self, main_module):
        m = main_module
        m._ai_cache_put("kh", "finance", "q", "ответ")
        m._ai_cache_get("kh")
        m._ai_cache_get("kh")
        m._ai_cache_get("kh")
        with m.db() as conn:
            hc = conn.execute("SELECT hit_count FROM ai_cache WHERE key=?", ("kh",)).fetchone()[0]
        assert hc == 3

    def test_replace_preserves_hit_count(self, main_module):
        m = main_module
        m._ai_cache_put("kr", "finance", "q", "v1")
        m._ai_cache_get("kr")
        m._ai_cache_get("kr")
        m._ai_cache_put("kr", "finance", "q", "v2")  # обновили
        assert m._ai_cache_get("kr") == "v2"
        with m.db() as conn:
            hc = conn.execute("SELECT hit_count FROM ai_cache WHERE key=?", ("kr",)).fetchone()[0]
        # 2 hit'а до replace + 1 после get выше
        assert hc == 3

    def test_ttl_expired_returns_none(self, main_module):
        m = main_module
        m._ai_cache_put("kt", "finance", "q", "ответ")
        # Прокручиваем created_at на 25 часов назад
        with m.db() as conn:
            conn.execute(
                "UPDATE ai_cache SET created_at=datetime('now','-25 hours') WHERE key=?",
                ("kt",),
            )
        assert m._ai_cache_get("kt") is None


class TestCacheAdminEndpoints:
    def test_stats_requires_admin(self, client):
        r = client.get("/admin/ai-cache/stats")
        assert r.status_code == 403

    def test_clear_requires_admin(self, client):
        r = client.post("/admin/ai-cache/clear")
        assert r.status_code == 403

    def test_stats_with_admin_token(self, client, admin_token, main_module):
        main_module._ai_cache_put("ks1", "finance", "q1", "a1")
        main_module._ai_cache_put("ks2", "wb", "q2", "a2")
        r = client.get("/admin/ai-cache/stats", headers={"X-Admin-Token": admin_token})
        assert r.status_code == 200
        data = r.json()
        assert data["total_entries"] == 2
        assert data["fresh_24h"] == 2
        assert "top_questions" in data
        assert "hit_rate_7d_pct" in data

    def test_clear_with_admin_token(self, client, admin_token, main_module):
        main_module._ai_cache_put("kc1", "finance", "q1", "a1")
        main_module._ai_cache_put("kc2", "wb", "q2", "a2")
        r = client.post("/admin/ai-cache/clear", headers={"X-Admin-Token": admin_token})
        assert r.status_code == 200
        assert r.json()["cleared"] == 2
        assert main_module._ai_cache_get("kc1") is None


class TestStreamReplay:
    def test_replay_yields_chunks_and_final(self, main_module):
        chunks = list(main_module._ai_cache_stream_replay("Привет мир"))
        assert len(chunks) >= 2
        # Последняя строка должна содержать done:true и cache_hit:true
        last = chunks[-1].decode("utf-8")
        assert '"done": true' in last
        assert '"cache_hit": true' in last
