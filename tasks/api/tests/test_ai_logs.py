"""Тесты для ai_logs — endpoints и helper-функция логирования."""


def _insert_log(main_module, **kwargs):
    """Прямая вставка строки в ai_logs минуя проксю (для теста endpoint'ов)."""
    with main_module.db() as conn:
        conn.execute(
            "INSERT INTO ai_logs(ts,dashboard,user_token_short,question,response,"
            "prompt_tokens,completion_tokens,latency_ms,cache_hit,error) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                kwargs.get("ts") or "2026-05-18 10:00:00",
                kwargs.get("dashboard", "finance"),
                kwargs.get("token_short", ""),
                kwargs.get("question", "вопрос"),
                kwargs.get("response", "ответ"),
                kwargs.get("prompt_tokens", 100),
                kwargs.get("completion_tokens", 50),
                kwargs.get("latency_ms", 1234),
                1 if kwargs.get("cache_hit") else 0,
                kwargs.get("error", ""),
            ),
        )


class TestAiLogsEndpoints:
    def test_logs_require_admin(self, client):
        assert client.get("/admin/ai-logs").status_code in (401, 403)
        assert client.get("/admin/ai-logs/stats").status_code in (401, 403)

    def test_logs_empty_with_admin(self, client, admin_token):
        r = client.get("/admin/ai-logs", headers={"X-Auth-Token": admin_token})
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, (list, dict))

    def test_logs_stats_returns_structure(self, client, admin_token, main_module):
        _insert_log(main_module, dashboard="finance", cache_hit=False)
        _insert_log(main_module, dashboard="finance", cache_hit=True)
        _insert_log(main_module, dashboard="wb", cache_hit=False)
        r = client.get("/admin/ai-logs/stats", headers={"X-Auth-Token": admin_token})
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict)

    def test_logs_filter_by_dashboard(self, client, admin_token, main_module):
        _insert_log(main_module, dashboard="finance", question="фин-вопрос")
        _insert_log(main_module, dashboard="wb", question="wb-вопрос")
        r = client.get("/admin/ai-logs?dashboard=finance", headers={"X-Auth-Token": admin_token})
        assert r.status_code == 200


class TestAiLogsSchema:
    def test_table_exists(self, main_module):
        with main_module.db() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='ai_logs'"
            ).fetchone()
        assert row is not None

    def test_required_columns(self, main_module):
        with main_module.db() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(ai_logs)").fetchall()}
        expected = {"ts", "dashboard", "question", "response", "latency_ms", "cache_hit", "error"}
        assert expected.issubset(cols)

    def test_insert_and_select(self, main_module):
        _insert_log(main_module, dashboard="test", question="q", response="r", cache_hit=True)
        with main_module.db() as conn:
            row = conn.execute(
                "SELECT dashboard,cache_hit FROM ai_logs WHERE dashboard='test'"
            ).fetchone()
        assert row["dashboard"] == "test"
        assert row["cache_hit"] == 1
