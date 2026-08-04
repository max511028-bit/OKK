"""Тендер-радар: схема, отбор, API вкладки.

Сеть НЕ задействована — коннекторы подменяются заглушкой. Живые прогоны
по площадкам делались вручную при переносе (Bidzaar: 1327 карточек → 24
подошли), здесь закрепляем логику, которая не должна сломаться молча.

Отдельно проверяется то, на чём я обжёгся при переносе 31.07:
  • падение записи не должно выглядеть как «ok, найдено 0»;
  • правка фильтров не должна удалять уже собранные тендеры.
"""
import datetime as dt
import importlib
import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))


@pytest.fixture()
def tr(tmp_path, monkeypatch):
    """Свежая база на каждый тест + перезагруженные модули под неё."""
    monkeypatch.setenv("TR_DB_PATH", str(tmp_path / "tenders.db"))
    monkeypatch.setenv("PORTAL_PASSWORD", "testpass")
    # Прогрев в фоне держал бы файл предыдущего теста, и следующий
    # падал на «database is locked» — в тестах он не нужен.
    monkeypatch.setenv("TR_NO_WARMUP", "1")
    import tenders_core.config as cfg
    importlib.reload(cfg)
    import tenders_db as tdb
    importlib.reload(tdb)
    import tenders_pipeline as pipe
    importlib.reload(pipe)
    # tenders_api инициализирует схему и реестр площадок ЛЕНИВО, один раз
    # на процесс. В бою база не меняется, а в тестах она своя на каждый
    # тест — поэтому модуль перезагружаем, иначе флаг «уже готово»
    # переживёт подмену пути и реестр окажется пустым.
    import tenders_api as tapi
    importlib.reload(tapi)
    tdb.init_db()
    return tdb, pipe


def _direction(tdb, **over):
    """Направление с одним словом «персонал» и заданными фильтрами."""
    fields = {"cities": [], "customers": [], "regions": [], "laws": [],
              "okpd2": [], "source_codes": [], "min_price": None, "max_price": None}
    fields.update(over)
    with tdb.db() as conn:
        cur = conn.execute(
            "INSERT INTO directions(group_id, name, description, is_active, sort_order, "
            "min_score, min_price, max_price, regions, cities, customers, laws, okpd2, "
            "source_codes, created_at) VALUES(NULL,'Тест','',1,0,1.0,?,?,?,?,?,?,?,?,?)",
            (fields["min_price"], fields["max_price"], tdb.dumps(fields["regions"]),
             tdb.dumps(fields["cities"]), tdb.dumps(fields["customers"]),
             tdb.dumps(fields["laws"]), tdb.dumps(fields["okpd2"]),
             tdb.dumps(fields["source_codes"]), tdb.now_iso()))
        did = cur.lastrowid
        conn.execute(
            "INSERT INTO keywords(direction_id, phrase, kind, weight, match_mode, is_active) "
            "VALUES(?, 'персонал', 'include', 2.0, 'stem', 1)", (did,))
    return did


class TestSchema:
    def test_tables_created(self, tr):
        tdb, _ = tr
        with tdb.db() as conn:
            names = {r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("groups", "directions", "keywords", "sources", "tenders",
                  "matches", "runs", "settings"):
            assert t in names, t

    def test_init_is_idempotent(self, tr):
        """init_db зовётся из каждого запроса лениво — повторный вызов
        не должен ничего ломать."""
        tdb, _ = tr
        tdb.init_db()
        tdb.init_db()
        with tdb.db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] >= 2

    def test_default_interval_present(self, tr):
        tdb, _ = tr
        with tdb.db() as conn:
            assert int(tdb.get_setting(conn, "scan_interval_minutes")) > 0


class TestMatchingFilters:
    """Фильтры направления. Город и заказчик добавлены 31.07 по просьбе
    владельца — их поведение сознательно разное."""

    def _match(self, tdb, tender, **over):
        import tenders_pipeline as pipe
        did = _direction(tdb, **over)
        with tdb.db() as conn:
            dirs = pipe._load_directions(conn)
        from tenders_core.matching import match_tender
        return match_tender(tender, dirs), did

    BASE = {"title": "Услуги по предоставлению персонала на склад",
            "description": "", "customer": 'ООО "МАГНИТ"', "region": "Москва",
            "price": 100000.0, "law": "commercial", "okpd2": "",
            "source_code": "bidzaar", "purchase_method": ""}

    def test_matches_without_filters(self, tr):
        tdb, _ = tr
        res, _ = self._match(tdb, self.BASE)
        assert res and res[0].score == 2.0

    def test_city_matches_by_region(self, tr):
        tdb, _ = tr
        res, _ = self._match(tdb, self.BASE, cities=["Москва"])
        assert res

    def test_city_filters_out_other(self, tr):
        tdb, _ = tr
        res, _ = self._match(tdb, self.BASE, cities=["Владивосток"])
        assert not res

    def test_city_found_in_customer_field(self, tr):
        """Площадки кладут город куда попало — поэтому ищем широко.
        Здесь регион пустой, а город назван в заказчике."""
        tdb, _ = tr
        t = dict(self.BASE, region="", customer="ООО Ромашка, г. Пермь")
        res, _ = self._match(tdb, t, cities=["Пермь"])
        assert res

    def test_customer_matches_exactly_that_field(self, tr):
        tdb, _ = tr
        res, _ = self._match(tdb, self.BASE, customers=["Магнит"])
        assert res

    def test_customer_ignores_text_mentions(self, tr):
        """Смысл фильтра — следить за компанией. Упоминание её в описании
        чужой закупки совпадением считаться не должно."""
        tdb, _ = tr
        t = dict(self.BASE, customer="ООО Вектор",
                 description="персонал для нужд сети Магнит")
        res, _ = self._match(tdb, t, customers=["Магнит"])
        assert not res

    def test_price_bounds(self, tr):
        tdb, _ = tr
        assert not self._match(tdb, self.BASE, min_price=500000.0)[0]
        assert self._match(tdb, self.BASE, max_price=500000.0)[0]

    def test_exclude_word_drops_tender(self, tr):
        tdb, _ = tr
        did = _direction(tdb)
        with tdb.db() as conn:
            conn.execute("INSERT INTO keywords(direction_id, phrase, kind, weight, "
                         "match_mode, is_active) VALUES(?, 'уборка', 'exclude', 1, 'stem', 1)",
                         (did,))
        import tenders_pipeline as pipe
        from tenders_core.matching import match_tender
        with tdb.db() as conn:
            dirs = pipe._load_directions(conn)
        t = dict(self.BASE, title="Услуги персонала и уборка помещений")
        assert not match_tender(t, dirs)


class _FakeSource:
    """Коннектор-заглушка: отдаёт заданные карточки, в сеть не ходит."""
    code = "fake"
    title = "Заглушка"
    site_url = ""
    location = "any"
    requires_auth = False
    enabled_by_default = True
    rows: list = []

    def fetch(self, since=None, settings=None, credentials=None, queries=None):
        return iter(self.rows)


def _raw(ext, title, **over):
    from tenders_core.sources.base import RawTender
    kw = {"customer": "ООО Ромашка", "region": "Москва", "price": 1000.0,
          "law": "commercial", "published_at": dt.datetime.now()}
    kw.update(over)
    return RawTender(external_id=ext, title=title, **kw)


class TestPipeline:
    def _prepare(self, tr, rows):
        tdb, pipe = tr
        _direction(tdb)
        _FakeSource.rows = rows
        pipe.get_source = lambda code: _FakeSource if code == "fake" else None
        with tdb.db() as conn:
            conn.execute("INSERT INTO sources(code, title, location, is_enabled, settings) "
                         "VALUES('fake','Заглушка','any',1,'{}')")
        return tdb, pipe

    def test_only_matching_tenders_are_stored(self, tr):
        tdb, pipe = self._prepare(tr, [
            _raw("1", "Услуги персонала на складе"),
            _raw("2", "Поставка канцелярских товаров")])
        res = pipe.run_source("fake", triggered_by="manual", depth_days=7)
        assert res["status"] == "ok"
        assert res["created"] == 1, "второй тендер не про персонал — хранить его незачем"

    def test_rerun_updates_not_duplicates(self, tr):
        tdb, pipe = self._prepare(tr, [_raw("1", "Услуги персонала на складе")])
        pipe.run_source("fake", depth_days=7)
        res = pipe.run_source("fake", depth_days=7)
        assert res["created"] == 0 and res["updated"] == 1
        with tdb.db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM tenders").fetchone()[0] == 1

    def test_write_failure_is_not_reported_as_ok(self, tr):
        """31.07: при переносе падала КАЖДАЯ запись, а прогон рапортовал
        «ok, найдено 0». Молчаливый ноль неотличим от честного «ничего не
        подошло» — худший вид поломки."""
        tdb, pipe = self._prepare(tr, [_raw("1", "Услуги персонала на складе")])

        def boom(*a, **k):
            raise RuntimeError("сломалось при записи")
        pipe._upsert = boom
        res = pipe.run_source("fake", depth_days=7)
        assert res["status"] == "partial"
        assert "не записано" in res["message"]

    def test_manual_run_records_depth_and_trigger(self, tr):
        tdb, pipe = self._prepare(tr, [_raw("1", "Услуги персонала на складе")])
        pipe.run_source("fake", triggered_by="manual", depth_days=30)
        with tdb.db() as conn:
            run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        assert run["triggered_by"] == "manual" and run["depth_days"] == 30

    def test_location_filter_skips_foreign_sources(self, tr):
        """ЕИС достижим только из домашней сети — сборщик на VPS его
        трогать не должен."""
        tdb, pipe = self._prepare(tr, [])
        with tdb.db() as conn:
            conn.execute("UPDATE sources SET location='home' WHERE code='fake'")
        assert pipe.run_all(location="vps") == []
        assert len(pipe.run_all(location="home")) == 1


class TestRematchIsReversible:
    """Правка фильтра не должна уничтожать собранное. Ловушка 31.07:
    сняв фильтр по заказчику, я получал 0 вместо прежних 165."""

    def test_tender_returns_after_filter_removed(self, tr):
        tdb, pipe = tr
        did = _direction(tdb)
        _FakeSource.rows = [_raw("1", "Услуги персонала на складе")]
        pipe.get_source = lambda code: _FakeSource if code == "fake" else None
        with tdb.db() as conn:
            conn.execute("INSERT INTO sources(code, title, location, is_enabled, settings) "
                         "VALUES('fake','Заглушка','any',1,'{}')")
        pipe.run_source("fake", depth_days=7)

        def matches_count():
            with tdb.db() as conn:
                return conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]

        def tenders_count():
            with tdb.db() as conn:
                return conn.execute("SELECT COUNT(*) FROM tenders").fetchone()[0]

        assert matches_count() == 1
        with tdb.db() as conn:  # сузили до города, которого нет
            conn.execute("UPDATE directions SET cities=? WHERE id=?",
                         (tdb.dumps(["Владивосток"]), did))
        pipe.rematch_all()
        assert matches_count() == 0, "не подходит — совпадений быть не должно"
        assert tenders_count() == 1, "но сам тендер удалять нельзя"

        with tdb.db() as conn:  # передумали
            conn.execute("UPDATE directions SET cities='[]' WHERE id=?", (did,))
        pipe.rematch_all()
        assert matches_count() == 1, "снял фильтр — находка обязана вернуться"


class TestApi:
    @pytest.fixture()
    def client(self, tr):
        from fastapi.testclient import TestClient
        import main
        importlib.reload(main)
        return TestClient(main.app)

    P = {"password": "testpass"}

    def test_password_required(self, client):
        assert client.get("/tenders/summary").status_code == 403
        assert client.get("/tenders/summary", params=self.P).status_code == 200

    def test_sources_registered_from_code(self, client):
        items = client.get("/tenders/sources", params=self.P).json()["items"]
        codes = {s["code"] for s in items}
        assert {"eis", "b2b_center", "bidzaar"} <= codes
        eis = next(s for s in items if s["code"] == "eis")
        assert eis["location"] == "home"

    def test_source_password_never_leaves_server(self, client):
        items = client.get("/tenders/sources", params=self.P).json()["items"]
        assert all("password" not in s for s in items)

    def test_group_direction_keyword_flow(self, client):
        gid = client.post("/tenders/groups", params=self.P,
                          json={"name": "Персонал"}).json()["id"]
        did = client.post("/tenders/directions", params=self.P, json={
            "group_id": gid, "name": "Склад", "cities": ["Москва"],
            "customers": ["Магнит"]}).json()["id"]
        client.put(f"/tenders/directions/{did}/keywords", params=self.P,
                   json=[{"phrase": "персонал", "kind": "include", "weight": 2}])
        tree = client.get("/tenders/groups", params=self.P).json()
        grp = tree["groups"][0]
        assert grp["name"] == "Персонал"
        d = grp["directions"][0]
        assert d["cities"] == ["Москва"] and d["customers"] == ["Магнит"]
        assert d["keywords_count"] == 1

    def test_deleting_group_keeps_directions(self, client):
        """Удаление папки не должно уносить настроенные правила."""
        gid = client.post("/tenders/groups", params=self.P,
                          json={"name": "Временная"}).json()["id"]
        client.post("/tenders/directions", params=self.P,
                    json={"group_id": gid, "name": "Склад"})
        client.delete(f"/tenders/groups/{gid}", params=self.P)
        tree = client.get("/tenders/groups", params=self.P).json()
        assert not tree["groups"]
        assert len(tree["orphans"]) == 1

    def test_interval_is_saved_and_clamped(self, client):
        got = client.put("/tenders/settings", params=self.P,
                         json={"scan_interval_minutes": 45}).json()
        assert got["scan_interval_minutes"] == 45
        low = client.put("/tenders/settings", params=self.P,
                         json={"scan_interval_minutes": 1}).json()
        assert low["scan_interval_minutes"] == 5, "слишком частый опрос забанят на площадках"

    def test_status_change_and_validation(self, client, tr):
        tdb, _ = tr
        with tdb.db() as conn:
            conn.execute(
                "INSERT INTO tenders(source_code, external_id, title, first_seen_at, updated_at) "
                "VALUES('fake','1','Тест',?,?)", (tdb.now_iso(), tdb.now_iso()))
        assert client.patch("/tenders/item/1", params=self.P,
                            json={"status": "interesting"}).status_code == 200
        assert client.patch("/tenders/item/1", params=self.P,
                            json={"status": "чепуха"}).status_code == 400

    def test_list_hides_tenders_without_matches(self, client, tr):
        """Тендер без совпадений остаётся в базе, но в выдаче не виден."""
        tdb, _ = tr
        with tdb.db() as conn:
            conn.execute(
                "INSERT INTO tenders(source_code, external_id, title, first_seen_at, updated_at) "
                "VALUES('fake','1','Тест',?,?)", (tdb.now_iso(), tdb.now_iso()))
        assert client.get("/tenders/list", params=self.P).json()["total"] == 0

    def test_export_returns_xlsx(self, client):
        r = client.get("/tenders/export", params=self.P)
        assert r.status_code == 200
        assert r.content[:2] == b"PK", "xlsx — это zip-контейнер"


class TestSearchQueries:
    """Подбор поисковых фраз. Замер 04.08 на B2B-Center: «персонал» даёт
    20 закупок, «аутсорсинг персонала» — НОЛЬ. Их поиск требует точного
    вхождения всей строки, и длинные фразы сами себе резали выдачу."""

    def _dirs(self, tdb, phrases):
        import tenders_pipeline as pipe
        with tdb.db() as conn:
            cur = conn.execute(
                "INSERT INTO directions(group_id,name,description,is_active,sort_order,"
                "min_score,regions,cities,customers,laws,okpd2,source_codes,created_at) "
                "VALUES(NULL,'Т','',1,0,1,'[]','[]','[]','[]','[]','[]',?)", (tdb.now_iso(),))
            did = cur.lastrowid
            for p, w in phrases:
                conn.execute("INSERT INTO keywords(direction_id,phrase,kind,weight,"
                             "match_mode,is_active) VALUES(?,?,'include',?,'stem',1)", (did, p, w))
        with tdb.db() as conn:
            return pipe._load_directions(conn)

    def test_phrases_kept_whole_by_default(self, tr):
        tdb, pipe = tr
        dirs = self._dirs(tdb, [("аутсорсинг персонала", 4)])
        assert pipe.search_queries_for(dirs, "bidzaar") == ["аутсорсинг персонала"]

    def test_split_into_words_for_query_driven(self, tr):
        tdb, pipe = tr
        dirs = self._dirs(tdb, [("аутсорсинг персонала", 4)])
        got = pipe.search_queries_for(dirs, "b2b_center", split_words=True)
        assert "персонала" in got and "аутсорсинг" in got
        assert "аутсорсинг персонала" not in got

    def test_generic_words_dropped(self, tr):
        """«Услуги» и «работы» есть в половине закупок страны — искать по
        ним значит получить случайную выдачу."""
        tdb, pipe = tr
        dirs = self._dirs(tdb, [("оказание услуг по предоставлению персонала", 4)])
        got = pipe.search_queries_for(dirs, "b2b_center", split_words=True)
        assert "персонала" in got
        for junk in ("услуг", "оказание", "предоставлению"):
            assert junk not in got, junk

    def test_hyphen_split_into_halves(self, tr):
        tdb, pipe = tr
        dirs = self._dirs(tdb, [("погрузочно-разгрузочные работы", 3)])
        got = pipe.search_queries_for(dirs, "b2b_center", split_words=True)
        assert "погрузочно" in got and "разгрузочные" in got

    def test_short_words_dropped(self, tr):
        tdb, pipe = tr
        dirs = self._dirs(tdb, [("уход за территорией", 2)])
        got = pipe.search_queries_for(dirs, "b2b_center", split_words=True)
        assert "за" not in got

    def test_exclude_words_never_searched(self, tr):
        tdb, pipe = tr
        import tenders_pipeline as p2
        with tdb.db() as conn:
            cur = conn.execute(
                "INSERT INTO directions(group_id,name,description,is_active,sort_order,"
                "min_score,regions,cities,customers,laws,okpd2,source_codes,created_at) "
                "VALUES(NULL,'Т','',1,0,1,'[]','[]','[]','[]','[]','[]',?)", (tdb.now_iso(),))
            did = cur.lastrowid
            conn.execute("INSERT INTO keywords(direction_id,phrase,kind,weight,match_mode,"
                         "is_active) VALUES(?,'персонал','include',2,'stem',1)", (did,))
            conn.execute("INSERT INTO keywords(direction_id,phrase,kind,weight,match_mode,"
                         "is_active) VALUES(?,'уборка','exclude',1,'stem',1)", (did,))
        with tdb.db() as conn:
            dirs = p2._load_directions(conn)
        assert "уборка" not in p2.search_queries_for(dirs, "b2b_center", split_words=True)
