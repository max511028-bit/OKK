"""Тесты авторизации write-эндпоинтов."""


def _task_payload(tid="test-1", title="Test"):
    return {
        "id": tid, "title": title, "desc": "", "system": "", "type": "",
        "priority": "med", "risk": "med", "assignee": "", "deadline": "",
        "status": "Не начато", "tz": "❌ Нет", "metric": "", "effect": "",
        "blockers": "Нет", "deps": "—", "history": [],
    }


class TestWriteAuth:
    def test_create_without_token_401(self, client):
        r = client.post("/tasks", json=_task_payload())
        assert r.status_code == 401

    def test_create_with_wrong_token_401(self, client):
        r = client.post("/tasks", json=_task_payload(), headers={"X-Auth-Token": "wrong"})
        assert r.status_code == 401

    def test_create_with_admin_token_for_portal_fails(self, client, admin_token):
        # admin токен НЕ должен проходить как portal
        r = client.post("/tasks", json=_task_payload(), headers={"X-Auth-Token": admin_token})
        assert r.status_code == 401

    def test_create_with_portal_token_succeeds(self, client, portal_token):
        r = client.post("/tasks", json=_task_payload("auth-1"), headers={"X-Auth-Token": portal_token})
        assert r.status_code == 200

    def test_delete_without_token_401(self, client, portal_token):
        client.post("/tasks", json=_task_payload("del-1"), headers={"X-Auth-Token": portal_token})
        r = client.delete("/tasks/del-1")
        assert r.status_code == 401

    def test_read_endpoints_open(self, client):
        # GET не требует токена
        assert client.get("/state").status_code == 200
        assert client.get("/tasks").status_code == 200


class TestAdminEndpointsAuth:
    def test_purge_trash_needs_admin_token(self, client):
        # remote (TestClient — не localhost) без токена → 403
        r = client.post("/admin/tasks/purge-trash")
        assert r.status_code in (403, 401)

    def test_purge_trash_with_admin_token(self, client, admin_token):
        r = client.post("/admin/tasks/purge-trash", headers={"X-Admin-Token": admin_token})
        assert r.status_code == 200
        assert r.json()["ok"] is True


class TestTokenDerivation:
    def test_make_token_stable_across_calls(self, main_module):
        assert main_module._make_token("admin") == main_module._make_token("admin")

    def test_admin_and_portal_tokens_differ(self, main_module):
        assert main_module._make_token("admin") != main_module._make_token("portal")

    def test_verify_token_round_trip(self, main_module):
        t = main_module._make_token("portal")
        assert main_module._verify_token(t, "portal") is True
        assert main_module._verify_token(t, "admin") is False
        assert main_module._verify_token("garbage", "portal") is False
