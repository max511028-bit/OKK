"""Конструктор сценариев: предупреждения при сохранении (2026-07) —
не блокируют сохранение, но подсвечивают баги, найденные на живых
обзвонах (слипшееся {name}, дубли crit, стоп без объяснения)."""


def _save(client, portal_token, sid, steps, closing="Спасибо, до свидания."):
    return client.put(
        f"/voicecall/scripts/{sid}",
        headers={"X-Auth-Token": portal_token},
        json={"name": "Тестовый сценарий", "steps": steps,
              "stop_factors": [], "closing": closing},
    )


def _create(client, portal_token, name="Черновик для теста"):
    r = client.post(
        "/voicecall/scripts",
        headers={"X-Auth-Token": portal_token},
        json={"name": name, "steps": [], "stop_factors": [], "closing": ""},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


class TestScenarioValidationWarnings:
    def test_clean_scenario_has_no_warnings(self, client, portal_token):
        sid = _create(client, portal_token)
        steps = [
            {"id": "step1", "crit": "Шаг 1", "bot": "Здравствуйте, {name}! Удобно говорить?",
             "expect": "yesno", "end_on_no": True, "on_no": "Понял, хорошего дня!"},
        ]
        r = _save(client, portal_token, sid, steps)
        assert r.status_code == 200, r.text
        assert r.json()["warnings"] == []

    def test_name_glued_without_separator_is_flagged(self, client, portal_token):
        sid = _create(client, portal_token)
        steps = [{"id": "step1", "crit": "Шаг 1", "bot": "{name}Здравствуйте!", "expect": "yesno"}]
        r = _save(client, portal_token, sid, steps)
        warnings = r.json()["warnings"]
        assert any("слипается" in w for w in warnings), warnings

    def test_name_with_comma_is_not_flagged(self, client, portal_token):
        sid = _create(client, portal_token)
        steps = [{"id": "step1", "crit": "Шаг 1", "bot": "{name}, здравствуйте!", "expect": "yesno"}]
        r = _save(client, portal_token, sid, steps)
        warnings = r.json()["warnings"]
        assert not any("слипается" in w for w in warnings), warnings

    def test_empty_bot_text_is_flagged(self, client, portal_token):
        sid = _create(client, portal_token)
        steps = [{"id": "step1", "crit": "Шаг 1", "bot": "", "expect": "yesno"}]
        r = _save(client, portal_token, sid, steps)
        warnings = r.json()["warnings"]
        assert any("пустой текст вопроса" in w for w in warnings), warnings

    def test_duplicate_crit_is_flagged(self, client, portal_token):
        sid = _create(client, portal_token)
        steps = [
            {"id": "step1", "crit": "Судимости", "bot": "Вопрос 1?", "expect": "yesno"},
            {"id": "step2", "crit": "Судимости", "bot": "Вопрос 2?", "expect": "yesno"},
        ]
        r = _save(client, portal_token, sid, steps)
        warnings = r.json()["warnings"]
        assert any("такой же crit" in w for w in warnings), warnings

    def test_stop_on_yes_without_explanation_is_flagged(self, client, portal_token):
        sid = _create(client, portal_token)
        steps = [{"id": "step1", "crit": "Судимости", "bot": "Были судимости?",
                   "expect": "yesno", "end_on_yes": True}]
        r = _save(client, portal_token, sid, steps)
        warnings = r.json()["warnings"]
        assert any("стоп при «да»" in w for w in warnings), warnings

    def test_stop_on_yes_with_stop_msg_is_not_flagged(self, client, portal_token):
        sid = _create(client, portal_token)
        steps = [{"id": "step1", "crit": "Судимости", "bot": "Были судимости?",
                   "expect": "yesno", "end_on_yes": True, "on_yes": "Спасибо за честность, не подходит."}]
        r = _save(client, portal_token, sid, steps)
        warnings = r.json()["warnings"]
        assert not any("стоп при «да»" in w for w in warnings), warnings

    def test_missing_closing_is_flagged(self, client, portal_token):
        sid = _create(client, portal_token)
        steps = [{"id": "step1", "crit": "Шаг 1", "bot": "Вопрос?", "expect": "yesno"}]
        r = _save(client, portal_token, sid, steps, closing="")
        warnings = r.json()["warnings"]
        assert any("прощания" in w for w in warnings), warnings

    def test_warnings_do_not_block_save(self, client, portal_token):
        """Предупреждения информационные — сохранение проходит успешно
        даже при наличии проблем (черновик может быть незавершён)."""
        sid = _create(client, portal_token)
        steps = [{"id": "step1", "crit": "Шаг 1", "bot": "", "expect": "yesno"}]
        r = _save(client, portal_token, sid, steps, closing="")
        assert r.status_code == 200
        assert r.json()["status"] in ("draft", "published")

    def test_create_endpoint_also_returns_warnings(self, client, portal_token):
        r = client.post(
            "/voicecall/scripts",
            headers={"X-Auth-Token": portal_token},
            json={"name": "Новый со шлейфом", "closing": "",
                  "steps": [{"id": "step1", "crit": "X", "bot": "", "expect": "yesno"}],
                  "stop_factors": []},
        )
        assert r.status_code == 200, r.text
        assert "warnings" in r.json()
        assert len(r.json()["warnings"]) >= 1
