"""Тесты soft-delete + cron purge."""


def _task_payload(tid):
    return {
        "id": tid, "title": f"Task {tid}", "desc": "", "system": "", "type": "",
        "priority": "med", "risk": "med", "assignee": "", "deadline": "",
        "status": "Не начато", "tz": "❌ Нет", "metric": "", "effect": "",
        "blockers": "Нет", "deps": "—", "history": [],
    }


class TestSoftDelete:
    def test_delete_marks_deleted_at(self, client, portal_token, main_module):
        client.post("/tasks", json=_task_payload("sd-1"), headers={"X-Auth-Token": portal_token})
        r = client.delete("/tasks/sd-1", headers={"X-Auth-Token": portal_token})
        assert r.status_code == 200
        assert r.json()["soft"] is True
        with main_module.db() as conn:
            row = conn.execute("SELECT deleted_at FROM tasks WHERE id=?", ("sd-1",)).fetchone()
        assert row["deleted_at"] is not None

    def test_deleted_task_hidden_from_state(self, client, portal_token):
        client.post("/tasks", json=_task_payload("sd-2"), headers={"X-Auth-Token": portal_token})
        client.delete("/tasks/sd-2", headers={"X-Auth-Token": portal_token})
        r = client.get("/state")
        ids = [t["id"] for t in r.json()["tasks"]]
        assert "sd-2" not in ids

    def test_restore_recovers_task(self, client, portal_token):
        client.post("/tasks", json=_task_payload("sd-3"), headers={"X-Auth-Token": portal_token})
        client.delete("/tasks/sd-3", headers={"X-Auth-Token": portal_token})
        r = client.post("/tasks/sd-3/restore", headers={"X-Auth-Token": portal_token})
        assert r.status_code == 200
        ids = [t["id"] for t in client.get("/state").json()["tasks"]]
        assert "sd-3" in ids


class TestPurgeTrash:
    def test_purge_removes_old_deleted(self, client, portal_token, admin_token, main_module):
        # Создаём задачу, удаляем, прокручиваем deleted_at на 10 дней назад
        client.post("/tasks", json=_task_payload("p-old"), headers={"X-Auth-Token": portal_token})
        client.delete("/tasks/p-old", headers={"X-Auth-Token": portal_token})
        with main_module.db() as conn:
            conn.execute(
                "UPDATE tasks SET deleted_at=datetime('now','-10 days') WHERE id=?", ("p-old",)
            )
        r = client.post("/admin/tasks/purge-trash?days=7", headers={"X-Admin-Token": admin_token})
        assert r.status_code == 200
        assert r.json()["purged"] >= 1
        with main_module.db() as conn:
            row = conn.execute("SELECT id FROM tasks WHERE id=?", ("p-old",)).fetchone()
        assert row is None

    def test_purge_keeps_recent_deleted(self, client, portal_token, admin_token, main_module):
        client.post("/tasks", json=_task_payload("p-fresh"), headers={"X-Auth-Token": portal_token})
        client.delete("/tasks/p-fresh", headers={"X-Auth-Token": portal_token})
        r = client.post("/admin/tasks/purge-trash?days=7", headers={"X-Admin-Token": admin_token})
        assert r.status_code == 200
        # Свежеудалённая не должна попасть в purge
        with main_module.db() as conn:
            row = conn.execute("SELECT id FROM tasks WHERE id=?", ("p-fresh",)).fetchone()
        assert row is not None

    def test_purge_keeps_live_tasks(self, client, portal_token, admin_token, main_module):
        client.post("/tasks", json=_task_payload("p-live"), headers={"X-Auth-Token": portal_token})
        client.post("/admin/tasks/purge-trash?days=0", headers={"X-Admin-Token": admin_token})
        with main_module.db() as conn:
            row = conn.execute("SELECT id FROM tasks WHERE id=?", ("p-live",)).fetchone()
        assert row is not None
