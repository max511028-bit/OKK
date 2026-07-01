"""Тесты для доработок обзвона: шаблон Excel, загрузка/ручной ввод с
known_answers + предпроверкой, диспетчер очереди (poll/claim/result),
воронка и экспорт отчёта.

Сценарий tander-sterlitamak-pack берётся из voicecall/scenarios/*.json
(файловый fallback _vt_load_scenario) — в тестовой БД voicecall_scripts
пуста, так что это ровно тот путь который реально используется.
"""
import io
import json

SCENARIO = "tander-sterlitamak-pack"


def _xlsx_bytes(headers, rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


class TestUploadTemplate:
    def test_returns_xlsx_with_crit_columns(self, client):
        r = client.get("/voicecall/upload-template", params={"scenario_id": SCENARIO})
        assert r.status_code == 200
        assert "spreadsheet" in r.headers["content-type"]
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(r.content))
        ws = wb.active
        headers = [c.value for c in next(ws.iter_rows(max_row=1))]
        assert headers[0] == "Имя"
        assert headers[1] == "Телефон"
        assert len(headers) > 2  # хотя бы один вопрос сценария


class TestUploadContactsPrecheck:
    def test_stop_factor_screens_out_without_calling(self, client):
        """Пол=женский в файле — известный стоп-фактор, контакт не должен
        попасть в очередь на звонок."""
        content = _xlsx_bytes(["Имя", "Телефон", "Пол"], [["Мария", "79991112233", "женский"]])
        r = client.post(
            "/voicecall/upload-contacts",
            files={"file": ("test.xlsx", content,
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"name": "Тест отсева", "scenario_id": SCENARIO, "source": "manual"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["screened_out"] == 1
        assert body["queued"] == 0

        contacts = client.get("/voicecall/contacts",
                               params={"campaign_id": body["campaign_id"]}).json()["items"]
        assert len(contacts) == 1
        assert contacts[0]["status"] == "skipped"
        assert contacts[0]["screen_out_reason"] is not None

    def test_fully_known_no_stop_marked_done_without_call(self, client):
        """Все ответы известны, стопа нет — контакт сразу 'done' без
        реального звонка (подтверждённое решение пользователя)."""
        headers = ["Имя", "Телефон", "intro", "Пол", "Возраст 18-45",
                   "Гражданство РФ", "Судимости 158/228/105", "День + ночь",
                   "Физнагрузка", "ЛМК", "Когда выйти", "Опыт"]
        row = ["Иван", "79992223344", "да", "мужской", "27", "да", "нет",
               "день+ночь", "да", "да", "завтра", "да"]
        content = _xlsx_bytes(headers, [row])
        r = client.post(
            "/voicecall/upload-contacts",
            files={"file": ("test.xlsx", content,
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"name": "Тест полного знания", "scenario_id": SCENARIO, "source": "manual"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["precheck_done"] == 1
        assert body["queued"] == 0

        contacts = client.get("/voicecall/contacts",
                               params={"campaign_id": body["campaign_id"]}).json()["items"]
        assert contacts[0]["status"] == "done"
        assert contacts[0]["verdict"] == "passed"
        assert contacts[0]["validation_id"] is not None

    def test_partial_or_unknown_stays_pending(self, client):
        content = _xlsx_bytes(["Имя", "Телефон"], [["Пётр", "79993334455"]])
        r = client.post(
            "/voicecall/upload-contacts",
            files={"file": ("test.xlsx", content,
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"name": "Тест очереди", "scenario_id": SCENARIO, "source": "manual"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["queued"] == 1
        contacts = client.get("/voicecall/contacts",
                               params={"campaign_id": body["campaign_id"]}).json()["items"]
        assert contacts[0]["status"] == "pending"


class TestManualEntry:
    def test_manual_entry_same_precheck_logic(self, client):
        r = client.post("/voicecall/manual-entry", json={
            "campaign_name": "Ручной тест",
            "scenario_id": SCENARIO,
            "source": "manual",
            "rows": [
                {"name": "Ольга", "phone": "79994445566", "Пол": "женский"},
                {"name": "Сергей", "phone": "79995556677"},
            ],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 2
        assert body["screened_out"] == 1
        assert body["queued"] == 1


class TestDispatchQueue:
    def _upload_one_pending(self, client, phone="79996667788"):
        content = _xlsx_bytes(["Имя", "Телефон"], [["Кандидат", phone]])
        r = client.post(
            "/voicecall/upload-contacts",
            files={"file": ("test.xlsx", content,
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"name": "Диспетчер тест", "scenario_id": SCENARIO, "source": "manual"},
        )
        return r.json()["campaign_id"]

    def test_start_dispatch_requires_password(self, client):
        cid = self._upload_one_pending(client)
        r = client.post(f"/voicecall/campaigns/{cid}/start-dispatch")
        assert r.status_code == 403

    def test_full_dispatch_flow(self, client, portal_token):
        cid = self._upload_one_pending(client, phone="79997778899")
        auth = {"X-Auth-Token": portal_token}

        # Нет заявки на обзвон -> poll ничего не находит
        r = client.get("/voicecall/dispatch/poll", headers=auth)
        assert r.json()["campaign_id"] is None

        # Жмём "Начать обзвон"
        r = client.post(f"/voicecall/campaigns/{cid}/start-dispatch", headers=auth)
        assert r.status_code == 200, r.text
        assert r.json()["pending"] == 1

        # Повторный старт пока не done/idle -> не 409, т.к. ещё 'requested' не 'running'... но пул сам его переключит
        # Агент опрашивает и забирает кампанию
        r = client.get("/voicecall/dispatch/poll", headers=auth)
        assert r.status_code == 200
        poll = r.json()
        assert poll["campaign_id"] == cid
        assert poll["scenario_id"] == SCENARIO

        # Пока кампания running - повторный start-dispatch должен быть отклонён
        r = client.post(f"/voicecall/campaigns/{cid}/start-dispatch", headers=auth)
        assert r.status_code == 409

        # Агент забирает контакт
        r = client.post("/voicecall/dispatch/claim", params={"campaign_id": cid}, headers=auth)
        assert r.status_code == 200
        claim = r.json()
        assert claim["contact_id"] is not None
        assert claim["phone"] == "79997778899"

        contacts = client.get("/voicecall/contacts", params={"campaign_id": cid}).json()["items"]
        assert contacts[0]["status"] == "calling"
        assert contacts[0]["attempts"] == 1

        # Очередь пуста на второй claim -> кампания переходит в done
        r = client.post("/voicecall/dispatch/claim", params={"campaign_id": cid}, headers=auth)
        assert r.json()["contact_id"] is None
        camps = client.get("/voicecall/campaigns").json()["items"]
        camp = next(c for c in camps if c["id"] == cid)
        assert camp["dispatch_state"] == "done"

        # Агент шлёт результат звонка
        r = client.post("/voicecall/dispatch/result", headers=auth, json={
            "contact_id": claim["contact_id"],
            "status": "answered_completed",
            "verdict": "passed",
            "stop_reason": None,
            "answers": {"Пол": "мужской"},
            "notes": {},
            "transcript": [{"who": "bot", "text": "Привет", "ts": "2026-01-01T00:00:00"}],
            "duration_s": 42.0,
        })
        assert r.status_code == 200, r.text

        contacts = client.get("/voicecall/contacts", params={"campaign_id": cid}).json()["items"]
        assert contacts[0]["status"] == "done"
        assert contacts[0]["last_call_status"] == "answered_completed"
        assert contacts[0]["verdict"] == "passed"
        assert contacts[0]["validation_id"] is not None

    def test_result_voicemail_maps_to_failed(self, client, portal_token):
        cid = self._upload_one_pending(client, phone="79998889900")
        auth = {"X-Auth-Token": portal_token}
        client.post(f"/voicecall/campaigns/{cid}/start-dispatch", headers=auth)
        client.get("/voicecall/dispatch/poll", headers=auth)
        claim = client.post("/voicecall/dispatch/claim", params={"campaign_id": cid},
                             headers=auth).json()

        r = client.post("/voicecall/dispatch/result", headers=auth, json={
            "contact_id": claim["contact_id"],
            "status": "voicemail",
            "error": "автоответчик поймали",
        })
        assert r.status_code == 200
        contacts = client.get("/voicecall/contacts", params={"campaign_id": cid}).json()["items"]
        assert contacts[0]["status"] == "failed"
        assert contacts[0]["last_call_status"] == "voicemail"


class TestFunnelAndExport:
    def test_funnel_counts(self, client):
        headers = ["Имя", "Телефон", "Пол"]
        content = _xlsx_bytes(headers, [
            ["А", "79991110000", "женский"],   # screened_out
            ["Б", "79991110001", ""],           # pending
        ])
        r = client.post(
            "/voicecall/upload-contacts",
            files={"file": ("t.xlsx", content,
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"name": "Воронка тест", "scenario_id": SCENARIO, "source": "manual"},
        )
        cid = r.json()["campaign_id"]
        funnel = client.get(f"/voicecall/campaigns/{cid}/funnel").json()
        assert funnel["loaded"] == 2
        assert funnel["screened_out"] == 1
        assert funnel["pending"] == 1

    def test_export_returns_valid_xlsx(self, client):
        content = _xlsx_bytes(["Имя", "Телефон"], [["В", "79991110002"]])
        r = client.post(
            "/voicecall/upload-contacts",
            files={"file": ("t.xlsx", content,
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"name": "Экспорт тест", "scenario_id": SCENARIO, "source": "manual"},
        )
        cid = r.json()["campaign_id"]
        r = client.get(f"/voicecall/campaigns/{cid}/export")
        assert r.status_code == 200
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(r.content))
        ws = wb.active
        headers = [c.value for c in next(ws.iter_rows(max_row=1))]
        assert headers[:2] == ["Имя", "Телефон"]
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        assert rows[0][0] == "В"
