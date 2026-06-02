"""Тесты для новых endpoints аналитика (Этапы 5 + 6 из analyst-upgrade.md).

Покрывает:
- /analyst/fetch: SSRF-защита (localhost, private), валидация схемы, 401 без токена,
  лимит 50 МБ через mock-ответ urlopen.
- /analyst/gsheet: успех с моком googleapiclient, sheet not found, 401 без токена.
- /analyst/projects: create/get/list/delete + read_token, view_count, лимит payload.
- DEFAULT_PROMPTS['analyst'] содержит маркеры режимов AUTO_BRIEF / EXPLAIN_METRIC и confidence.
"""
import io
import json
from unittest.mock import patch, MagicMock


# ──────────── /analyst/fetch ────────────

class TestAnalystFetch:
    def test_requires_auth(self, client):
        r = client.get("/analyst/fetch", params={"url": "https://example.com/x.csv"})
        assert r.status_code == 401

    def test_rejects_localhost(self, client, portal_token):
        r = client.get(
            "/analyst/fetch",
            params={"url": "http://localhost:8000/x.csv"},
            headers={"X-Auth-Token": portal_token},
        )
        assert r.status_code == 400
        assert "local" in r.text.lower() or "private" in r.text.lower()

    def test_rejects_127_loopback(self, client, portal_token):
        r = client.get(
            "/analyst/fetch",
            params={"url": "http://127.0.0.1/x.csv"},
            headers={"X-Auth-Token": portal_token},
        )
        assert r.status_code == 400

    def test_rejects_private_192(self, client, portal_token):
        r = client.get(
            "/analyst/fetch",
            params={"url": "http://192.168.1.1/x.csv"},
            headers={"X-Auth-Token": portal_token},
        )
        assert r.status_code == 400

    def test_rejects_non_http_scheme(self, client, portal_token):
        for url in ("file:///etc/passwd", "ftp://example.com/x", "gopher://x"):
            r = client.get(
                "/analyst/fetch",
                params={"url": url},
                headers={"X-Auth-Token": portal_token},
            )
            assert r.status_code == 400, f"expected 400 for {url}, got {r.status_code}"

    def test_empty_url_400(self, client, portal_token):
        r = client.get("/analyst/fetch", headers={"X-Auth-Token": portal_token})
        assert r.status_code == 400

    def test_success_csv(self, main_module, client, portal_token):
        fake_body = b"a,b,c\n1,2,3\n"
        fake_resp = MagicMock()
        fake_resp.headers = {"Content-Type": "text/csv"}
        # read() возвращает чанками
        chunks = [fake_body, b""]
        fake_resp.read = MagicMock(side_effect=chunks)
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = lambda s, *a: False
        with patch.object(main_module, "_is_private_host", return_value=False), \
                patch.object(main_module._urllib, "urlopen", return_value=fake_resp):
            r = client.get(
                "/analyst/fetch",
                params={"url": "https://example.com/data.csv"},
                headers={"X-Auth-Token": portal_token},
            )
        assert r.status_code == 200, r.text
        assert r.content == fake_body
        assert "data.csv" in r.headers.get("content-disposition", "")

    def test_too_large_413(self, main_module, client, portal_token):
        # Имитируем поток который превышает лимит
        big = b"x" * (1024 * 1024)
        # Симулируем 60 МБ — больше лимита 50 МБ
        call_count = {"n": 0}

        def fake_read(n):
            call_count["n"] += 1
            if call_count["n"] > 60:
                return b""
            return big
        fake_resp = MagicMock()
        fake_resp.headers = {"Content-Type": "application/octet-stream"}
        fake_resp.read = fake_read
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = lambda s, *a: False
        with patch.object(main_module, "_is_private_host", return_value=False), \
                patch.object(main_module._urllib, "urlopen", return_value=fake_resp):
            r = client.get(
                "/analyst/fetch",
                params={"url": "https://example.com/big.bin"},
                headers={"X-Auth-Token": portal_token},
            )
        assert r.status_code == 413


# ──────────── /analyst/gsheet ────────────

class TestAnalystGsheet:
    def test_requires_auth(self, client):
        r = client.get("/analyst/gsheet", params={"id": "abc"})
        assert r.status_code == 401

    def test_bad_id_400(self, client, portal_token):
        r = client.get(
            "/analyst/gsheet",
            params={"id": "bad id with spaces!@#"},
            headers={"X-Auth-Token": portal_token},
        )
        assert r.status_code == 400

    def test_success(self, main_module, client, portal_token):
        # Подделываем google sheets service
        fake_svc = MagicMock()
        meta_resp = {"sheets": [{"properties": {"title": "Sheet1"}}, {"properties": {"title": "Лист2"}}]}
        fake_svc.spreadsheets.return_value.get.return_value.execute.return_value = meta_resp
        values_resp = {"values": [["h1", "h2"], ["a", "b"], ["c", "d"]]}
        fake_svc.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = values_resp

        fake_rl = MagicMock()
        fake_rl.get_sheets_service.return_value = fake_svc
        with patch.object(main_module, "_recruiter_logic", return_value=fake_rl):
            r = client.get(
                "/analyst/gsheet",
                params={"id": "1abcDEF_-xyz"},
                headers={"X-Auth-Token": portal_token},
            )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["name"] == "Sheet1"
        assert data["columns"] == ["A", "B"]
        assert data["rows"] == [["h1", "h2"], ["a", "b"], ["c", "d"]]

    def test_sheet_not_found(self, main_module, client, portal_token):
        fake_svc = MagicMock()
        meta_resp = {"sheets": [{"properties": {"title": "Sheet1"}}]}
        fake_svc.spreadsheets.return_value.get.return_value.execute.return_value = meta_resp
        fake_rl = MagicMock()
        fake_rl.get_sheets_service.return_value = fake_svc
        with patch.object(main_module, "_recruiter_logic", return_value=fake_rl):
            r = client.get(
                "/analyst/gsheet",
                params={"id": "1abcDEF_-xyz", "sheet": "Несуществующий"},
                headers={"X-Auth-Token": portal_token},
            )
        assert r.status_code == 404

    def test_service_unavailable(self, main_module, client, portal_token):
        fake_rl = MagicMock()
        fake_rl.get_sheets_service.return_value = None
        with patch.object(main_module, "_recruiter_logic", return_value=fake_rl):
            r = client.get(
                "/analyst/gsheet",
                params={"id": "1abcDEF_-xyz"},
                headers={"X-Auth-Token": portal_token},
            )
        assert r.status_code == 503

    def test_extracts_id_from_url(self, main_module, client, portal_token):
        fake_svc = MagicMock()
        fake_svc.spreadsheets.return_value.get.return_value.execute.return_value = {
            "sheets": [{"properties": {"title": "S"}}]
        }
        fake_svc.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {"values": []}
        fake_rl = MagicMock()
        fake_rl.get_sheets_service.return_value = fake_svc
        with patch.object(main_module, "_recruiter_logic", return_value=fake_rl):
            r = client.get(
                "/analyst/gsheet",
                params={"id": "https://docs.google.com/spreadsheets/d/1abc_XYZ-99/edit"},
                headers={"X-Auth-Token": portal_token},
            )
        assert r.status_code == 200


# ──────────── /analyst/projects ────────────

class TestAnalystProjects:
    def test_create_requires_auth(self, client):
        r = client.post("/analyst/projects", json={"name": "x", "payload": {}})
        assert r.status_code == 401

    def test_create_returns_id_and_token(self, client, portal_token):
        r = client.post(
            "/analyst/projects",
            json={"name": "My project", "payload": {"datasets": [], "artifacts": []}},
            headers={"X-Auth-Token": portal_token},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert len(data["id"]) >= 16
        assert len(data["read_token"]) >= 10
        assert data["name"] == "My project"

    def test_get_without_token_401(self, client, portal_token):
        r = client.post(
            "/analyst/projects",
            json={"name": "p", "payload": {"k": "v"}},
            headers={"X-Auth-Token": portal_token},
        )
        pid = r.json()["id"]
        r2 = client.get(f"/analyst/projects/{pid}")
        assert r2.status_code == 401

    def test_get_with_wrong_token_403(self, client, portal_token):
        r = client.post(
            "/analyst/projects",
            json={"name": "p", "payload": {"k": "v"}},
            headers={"X-Auth-Token": portal_token},
        )
        pid = r.json()["id"]
        r2 = client.get(f"/analyst/projects/{pid}", params={"t": "wrong"})
        assert r2.status_code == 403

    def test_get_with_token_returns_payload(self, client, portal_token):
        payload = {"datasets": [{"name": "a"}], "artifacts": [{"id": 1}]}
        r = client.post(
            "/analyst/projects",
            json={"name": "p", "payload": payload},
            headers={"X-Auth-Token": portal_token},
        )
        body = r.json()
        pid, tok = body["id"], body["read_token"]
        r2 = client.get(f"/analyst/projects/{pid}", params={"t": tok})
        assert r2.status_code == 200
        assert r2.json()["payload"] == payload

    def test_view_count_increments(self, client, portal_token):
        r = client.post(
            "/analyst/projects",
            json={"name": "p", "payload": {}},
            headers={"X-Auth-Token": portal_token},
        )
        body = r.json()
        pid, tok = body["id"], body["read_token"]
        v1 = client.get(f"/analyst/projects/{pid}", params={"t": tok}).json()["view_count"]
        v2 = client.get(f"/analyst/projects/{pid}", params={"t": tok}).json()["view_count"]
        v3 = client.get(f"/analyst/projects/{pid}", params={"t": tok}).json()["view_count"]
        assert v1 == 1 and v2 == 2 and v3 == 3

    def test_list_returns_owner_projects(self, client, portal_token):
        for i in range(3):
            client.post(
                "/analyst/projects",
                json={"name": f"p{i}", "payload": {"i": i}},
                headers={"X-Auth-Token": portal_token},
            )
        r = client.get("/analyst/projects", headers={"X-Auth-Token": portal_token})
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) >= 3
        # Свежие сверху
        names = [it["name"] for it in items[:3]]
        assert "p2" in names and "p1" in names and "p0" in names

    def test_list_requires_auth(self, client):
        r = client.get("/analyst/projects")
        assert r.status_code == 401

    def test_delete(self, client, portal_token):
        r = client.post(
            "/analyst/projects",
            json={"name": "p", "payload": {}},
            headers={"X-Auth-Token": portal_token},
        )
        pid = r.json()["id"]
        r2 = client.delete(f"/analyst/projects/{pid}", headers={"X-Auth-Token": portal_token})
        assert r2.status_code == 200
        # после удаления — 404
        tok = "x"
        r3 = client.get(f"/analyst/projects/{pid}", params={"t": tok})
        assert r3.status_code == 404

    def test_payload_too_large(self, client, portal_token):
        big = {"data": "x" * (6 * 1024 * 1024)}
        r = client.post(
            "/analyst/projects",
            json={"name": "big", "payload": big},
            headers={"X-Auth-Token": portal_token},
        )
        assert r.status_code == 413


# ──────────── DEFAULT_PROMPTS['analyst'] обновлён ────────────

class TestAnalystPrompt:
    def test_has_auto_brief_mode(self, main_module):
        p = main_module.DEFAULT_PROMPTS["analyst"]
        assert "MODE:AUTO_BRIEF" in p
        assert "observations" in p
        assert "suggested_charts" in p

    def test_has_explain_metric_mode(self, main_module):
        p = main_module.DEFAULT_PROMPTS["analyst"]
        assert "MODE:EXPLAIN_METRIC" in p
        assert "breakdowns" in p

    def test_has_confidence_requirement(self, main_module):
        p = main_module.DEFAULT_PROMPTS["analyst"]
        assert "confidence" in p
        # высокий/средний/низкий уровень
        assert "high" in p and "medium" in p and "low" in p

    def test_prompt_endpoint_returns_analyst(self, client):
        r = client.get("/ai/prompt/analyst")
        assert r.status_code == 200
        data = r.json()
        assert data["dashboard"] == "analyst"
        assert "MODE:AUTO_BRIEF" in data["prompt"]
