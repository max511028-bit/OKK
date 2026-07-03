"""Тесты доработок трекера задач по ТЗ: поля newDueDate/startDate/createdAt
в модели Task (перенос срока без затирания исходного дедлайна) и
/health-snapshot для тренда IT Health Score на дашборде.
"""


class TestNewDueDateFields:
    def test_task_accepts_and_returns_new_fields(self, client, portal_token):
        auth = {"X-Auth-Token": portal_token}
        task = {
            "id": "IT-900", "title": "Тест переноса срока",
            "deadline": "01.06.2026", "newDueDate": "15.06.2026",
            "startDate": "20.05.2026", "createdAt": "10.05.2026",
        }
        r = client.post("/tasks", headers=auth, json=task)
        assert r.status_code == 200, r.text
        saved = r.json()
        assert saved["deadline"] == "01.06.2026"      # исходный срок не затёрт
        assert saved["newDueDate"] == "15.06.2026"
        assert saved["startDate"] == "20.05.2026"
        assert saved["createdAt"] == "10.05.2026"

        state = client.get("/state").json()
        got = next(t for t in state["tasks"] if t["id"] == "IT-900")
        assert got["newDueDate"] == "15.06.2026"

    def test_old_task_without_new_fields_survives(self, client, portal_token):
        """Обратная совместимость: задача без newDueDate создаётся и читается,
        поля приходят пустыми строками (дефолты модели), ничего не падает."""
        auth = {"X-Auth-Token": portal_token}
        r = client.post("/tasks", headers=auth, json={"id": "IT-901", "title": "Старая задача"})
        assert r.status_code == 200, r.text
        saved = r.json()
        assert saved.get("newDueDate", "") == ""
        assert saved.get("startDate", "") == ""


class TestHealthSnapshot:
    def test_snapshot_saved_and_returned_in_state(self, client):
        r = client.post("/health-snapshot", json={"score": 82})
        assert r.status_code == 200, r.text
        hist = client.get("/state").json()["health_history"]
        assert len(hist) == 1
        assert hist[0]["score"] == 82

    def test_one_snapshot_per_day_last_wins(self, client):
        client.post("/health-snapshot", json={"score": 82})
        client.post("/health-snapshot", json={"score": 75})
        hist = client.get("/state").json()["health_history"]
        assert len(hist) == 1
        assert hist[0]["score"] == 75

    def test_score_clamped(self, client):
        client.post("/health-snapshot", json={"score": 250})
        hist = client.get("/state").json()["health_history"]
        assert hist[0]["score"] == 100
