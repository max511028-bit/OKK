"""Индикатор «жив ли агент обзвона»: /voicecall/agent-status считает
агента онлайн, если любой его запрос (poll/claim/result/...) был меньше
90 секунд назад."""


class TestAgentStatus:
    def test_offline_by_default(self, client):
        r = client.get("/voicecall/agent-status")
        assert r.status_code == 200
        d = r.json()
        assert d["online"] is False

    def test_online_after_agent_poll(self, client, portal_token):
        client.get("/voicecall/dispatch/poll",
                   headers={"X-Auth-Token": portal_token})
        d = client.get("/voicecall/agent-status").json()
        assert d["online"] is True
        assert d["last_seen"]
