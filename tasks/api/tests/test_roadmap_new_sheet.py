"""Тесты для нового способа загрузки дорожной карты — через Sheets API includeGridData.
Покрытие:
- _extract_hyperlink: явный hyperlink / formula / textFormatRuns / пусто
- load_projects_list: парсинг шапки, пропуск пустых строк, fallback к "Клиент+Проект", кэш
- константа ROADMAP_SHEET_ID указывает на новую таблицу
"""
import time
import pytest
from unittest.mock import patch, MagicMock

from tasks.api import recruiter_logic as rl


def test_roadmap_sheet_id_is_new():
    assert rl.ROADMAP_SHEET_ID == "1yhXUAQ3mY9-ZhFRmvtLGiXJoSRdU1gg6_utCJ_3aBao"
    assert rl.ROADMAP_TAB == "Шаблон"
    assert rl.ROADMAP_COL_LINK == "Ссылка на ТЗ"


class TestExtractHyperlink:
    def test_empty_cell(self):
        assert rl._extract_hyperlink({}) == ""
        assert rl._extract_hyperlink(None) == ""

    def test_explicit_hyperlink_field(self):
        cell = {"formattedValue": "Тандер Пермь",
                "hyperlink": "https://docs.google.com/spreadsheets/d/ABC123/edit"}
        assert rl._extract_hyperlink(cell) == "https://docs.google.com/spreadsheets/d/ABC123/edit"

    def test_hyperlink_with_whitespace_is_stripped(self):
        cell = {"hyperlink": "  https://example.com/x  "}
        assert rl._extract_hyperlink(cell) == "https://example.com/x"

    def test_formula_hyperlink_fallback(self):
        cell = {
            "formattedValue": "ТЗ Тольятти",
            "userEnteredValue": {"formulaValue": '=HYPERLINK("https://docs.google.com/spreadsheets/d/XYZ/edit","ТЗ Тольятти")'}
        }
        assert "XYZ" in rl._extract_hyperlink(cell)

    def test_formula_hyperlink_case_insensitive(self):
        cell = {"userEnteredValue": {"formulaValue": '=hyperlink("https://x.test/y","label")'}}
        assert rl._extract_hyperlink(cell) == "https://x.test/y"

    def test_text_format_runs_fallback(self):
        cell = {
            "formattedValue": "T",
            "textFormatRuns": [{"format": {"link": {"uri": "https://run.example/"}}}]
        }
        assert rl._extract_hyperlink(cell) == "https://run.example/"

    def test_text_format_runs_no_link(self):
        cell = {"textFormatRuns": [{"format": {"bold": True}}]}
        assert rl._extract_hyperlink(cell) == ""

    def test_priority_hyperlink_over_formula(self):
        cell = {
            "hyperlink": "https://primary/",
            "userEnteredValue": {"formulaValue": '=HYPERLINK("https://secondary/","x")'}
        }
        assert rl._extract_hyperlink(cell) == "https://primary/"

    def test_smart_chip_uri(self):
        """Google Smart Chip — «кнопка»-ссылка на Google-документ (chipRuns)."""
        cell = {
            "userEnteredValue": {"stringValue": "Тандер Дмитров"},
            "formattedValue": "Тандер Дмитров",
            "chipRuns": [
                {"chip": {"richLinkProperties": {
                    "uri": "https://docs.google.com/spreadsheets/d/1L0Li1gujPRzqXhWrXnX2bKddJE8RPh381LLvMDSzpZE/edit#gid=987057720",
                    "mimeType": "application/vnd.google-apps.ritz",
                }}},
                {"startIndex": 14},
            ],
        }
        url = rl._extract_hyperlink(cell)
        assert "1L0Li1gujPRzqXhWrXnX2bKddJE8RPh381LLvMDSzpZE" in url
        assert url.startswith("https://docs.google.com/spreadsheets/d/")

    def test_smart_chip_empty(self):
        """chipRuns без richLinkProperties — например, just startIndex marker."""
        cell = {"chipRuns": [{"startIndex": 5}]}
        assert rl._extract_hyperlink(cell) == ""

    def test_smart_chip_priority_lower_than_hyperlink(self):
        """Если есть и hyperlink, и chipRuns — приоритет у hyperlink."""
        cell = {
            "hyperlink": "https://primary/",
            "chipRuns": [{"chip": {"richLinkProperties": {"uri": "https://chip/"}}}],
        }
        assert rl._extract_hyperlink(cell) == "https://primary/"


def _mk_cell(text="", hyperlink=None, formula=None):
    c = {"formattedValue": text}
    if hyperlink:
        c["hyperlink"] = hyperlink
    if formula:
        c["userEnteredValue"] = {"formulaValue": formula}
    return c


def _mk_row(values):
    return {"values": values}


def _mk_sheets_response(header, data_rows):
    return {
        "sheets": [{
            "data": [{
                "rowData": [_mk_row([_mk_cell(h) for h in header])] + data_rows
            }]
        }]
    }


class TestLoadProjectsList:
    def setup_method(self):
        # очищаем кэш перед каждым тестом
        with rl._cache_lock:
            rl._projects_cache = None
            rl._projects_ts = 0.0

    def _stub_service(self, response):
        svc = MagicMock()
        svc.spreadsheets.return_value.get.return_value.execute.return_value = response
        return svc

    def test_returns_empty_when_no_service(self):
        with patch.object(rl, "get_sheets_service", return_value=None):
            assert rl.load_projects_list() == {}

    def test_basic_extraction(self):
        header = ["Клиент", "Проект", "География (ближайший крупный город)", "Дата запуска рекламы", "",
                  "Ответственный маркетолог", "Вахта", "Ставки за смену", "Ссылка на ТЗ",
                  "Текущий статус", "Наименование проекта", "Медиаплан"]
        rows = [
            _mk_row([
                _mk_cell("Тандер"),
                _mk_cell("Первоуральск"),
                _mk_cell("Екатеринбург"),
                _mk_cell(""), _mk_cell(""), _mk_cell("Кругленко"),
                _mk_cell("Коцюба"), _mk_cell("2500-5000"),
                _mk_cell("Тандер Первоуральск", hyperlink="https://docs.google.com/spreadsheets/d/AAA111/edit"),
                _mk_cell("Запущен"),
                _mk_cell("Тандер Первоуральск"),
                _mk_cell(""),
            ]),
            _mk_row([
                _mk_cell("Леруа"),
                _mk_cell("Подольск"),
                _mk_cell("Москва"),
                _mk_cell(""), _mk_cell(""), _mk_cell("Шевченко"),
                _mk_cell("Иванов"), _mk_cell("3000"),
                _mk_cell("ТЗ Подольск", hyperlink="https://docs.google.com/spreadsheets/d/BBB222/edit?gid=0"),
                _mk_cell("Пауза"),
                _mk_cell("Леруа Подольск"),
                _mk_cell(""),
            ]),
        ]
        resp = _mk_sheets_response(header, rows)
        with patch.object(rl, "get_sheets_service", return_value=self._stub_service(resp)):
            projects = rl.load_projects_list()
        assert set(projects.keys()) == {"Тандер Первоуральск", "Леруа Подольск"}
        assert projects["Тандер Первоуральск"]["tz_id"] == "AAA111"
        assert projects["Тандер Первоуральск"]["client"] == "Тандер"
        assert projects["Тандер Первоуральск"]["city"] == "Первоуральск"
        assert projects["Тандер Первоуральск"]["status"] == "Запущен"
        assert projects["Леруа Подольск"]["tz_id"] == "BBB222"
        assert projects["Леруа Подольск"]["status"] == "Пауза"

    def test_skips_rows_without_hyperlink(self):
        header = ["Клиент", "Проект", "География (ближайший крупный город)", "Дата запуска рекламы", "",
                  "Ответственный маркетолог", "Вахта", "Ставки за смену", "Ссылка на ТЗ",
                  "Текущий статус", "Наименование проекта", "Медиаплан"]
        rows = [
            _mk_row([
                _mk_cell("Тандер"), _mk_cell("Город1"), _mk_cell(""), _mk_cell(""), _mk_cell(""),
                _mk_cell(""), _mk_cell(""), _mk_cell(""),
                _mk_cell("есть текст но без линка"),  # i=8 col Link — без hyperlink
                _mk_cell("Запущен"), _mk_cell("Тандер Город1"), _mk_cell(""),
            ]),
            _mk_row([
                _mk_cell("Леруа"), _mk_cell("Город2"), _mk_cell(""), _mk_cell(""), _mk_cell(""),
                _mk_cell(""), _mk_cell(""), _mk_cell(""),
                _mk_cell("ТЗ", hyperlink="https://docs.google.com/spreadsheets/d/OK123/edit"),
                _mk_cell("Запущен"), _mk_cell("Леруа Город2"), _mk_cell(""),
            ]),
        ]
        with patch.object(rl, "get_sheets_service", return_value=self._stub_service(_mk_sheets_response(header, rows))):
            p = rl.load_projects_list()
        assert list(p.keys()) == ["Леруа Город2"]
        assert p["Леруа Город2"]["tz_id"] == "OK123"

    def test_fallback_name_from_client_plus_project(self):
        header = ["Клиент", "Проект", "География (ближайший крупный город)", "Дата запуска рекламы", "",
                  "Ответственный маркетолог", "Вахта", "Ставки за смену", "Ссылка на ТЗ",
                  "Текущий статус", "Наименование проекта", "Медиаплан"]
        rows = [
            _mk_row([
                _mk_cell("Озон"), _mk_cell("Хоругвино"), _mk_cell(""), _mk_cell(""), _mk_cell(""),
                _mk_cell(""), _mk_cell(""), _mk_cell(""),
                _mk_cell("link", hyperlink="https://docs.google.com/spreadsheets/d/OZN/edit"),
                _mk_cell("Запущен"),
                _mk_cell(""),  # Наименование проекта пустое
                _mk_cell(""),
            ]),
        ]
        with patch.object(rl, "get_sheets_service", return_value=self._stub_service(_mk_sheets_response(header, rows))):
            p = rl.load_projects_list()
        assert "Озон Хоругвино" in p
        assert p["Озон Хоругвино"]["tz_id"] == "OZN"

    def test_handles_formula_hyperlink(self):
        header = ["Клиент", "Проект", "География (ближайший крупный город)", "Дата запуска рекламы", "",
                  "Ответственный маркетолог", "Вахта", "Ставки за смену", "Ссылка на ТЗ",
                  "Текущий статус", "Наименование проекта", "Медиаплан"]
        rows = [
            _mk_row([
                _mk_cell("X"), _mk_cell("Y"), _mk_cell(""), _mk_cell(""), _mk_cell(""),
                _mk_cell(""), _mk_cell(""), _mk_cell(""),
                _mk_cell("ТЗ X", formula='=HYPERLINK("https://docs.google.com/spreadsheets/d/FORMULA_ID_42/edit","ТЗ X")'),
                _mk_cell(""),
                _mk_cell("X Y"),
                _mk_cell(""),
            ]),
        ]
        with patch.object(rl, "get_sheets_service", return_value=self._stub_service(_mk_sheets_response(header, rows))):
            p = rl.load_projects_list()
        assert p["X Y"]["tz_id"] == "FORMULA_ID_42"

    def test_name_with_internal_tab_normalized(self):
        """Если внутри имени проекта есть \\t или повторные пробелы — заменяем на одиночный пробел."""
        header = ["Клиент", "Проект", "География (ближайший крупный город)", "Дата запуска рекламы", "",
                  "Ответственный маркетолог", "Вахта", "Ставки за смену", "Ссылка на ТЗ",
                  "Текущий статус", "Наименование проекта", "Медиаплан"]
        rows = [
            _mk_row([
                _mk_cell("Тандер"), _mk_cell("Дмитров"), _mk_cell(""), _mk_cell(""), _mk_cell(""),
                _mk_cell(""), _mk_cell(""), _mk_cell(""),
                _mk_cell("link", hyperlink="https://docs.google.com/spreadsheets/d/TID/edit"),
                _mk_cell("Запущен"),
                _mk_cell("Тандер\tДмитров"),  # таб внутри
                _mk_cell(""),
            ]),
        ]
        with patch.object(rl, "get_sheets_service", return_value=self._stub_service(_mk_sheets_response(header, rows))):
            p = rl.load_projects_list()
        assert "Тандер Дмитров" in p  # с обычным пробелом
        assert "Тандер\tДмитров" not in p
        assert p["Тандер Дмитров"]["tz_id"] == "TID"

    def test_handles_smart_chip_link(self):
        """Если ячейка содержит Smart Chip (chipRuns), проект должен подтянуться."""
        header = ["Клиент", "Проект", "География (ближайший крупный город)", "Дата запуска рекламы", "",
                  "Ответственный маркетолог", "Вахта", "Ставки за смену", "Ссылка на ТЗ",
                  "Текущий статус", "Наименование проекта", "Медиаплан"]
        chip_cell = {
            "userEnteredValue": {"stringValue": "Тандер Дмитров"},
            "formattedValue": "Тандер Дмитров",
            "chipRuns": [{"chip": {"richLinkProperties": {
                "uri": "https://docs.google.com/spreadsheets/d/CHIP_TZ_ID/edit#gid=987057720",
                "mimeType": "application/vnd.google-apps.ritz",
            }}}],
        }
        rows = [
            _mk_row([
                _mk_cell("Тандер"), _mk_cell("Дмитров"), _mk_cell(""), _mk_cell(""), _mk_cell(""),
                _mk_cell(""), _mk_cell(""), _mk_cell(""),
                chip_cell,
                _mk_cell("Запущен"),
                _mk_cell("Тандер Дмитров"),
                _mk_cell(""),
            ]),
        ]
        with patch.object(rl, "get_sheets_service", return_value=self._stub_service(_mk_sheets_response(header, rows))):
            p = rl.load_projects_list()
        assert p["Тандер Дмитров"]["tz_id"] == "CHIP_TZ_ID"
        assert p["Тандер Дмитров"]["client"] == "Тандер"

    def test_cache_hit_within_ttl(self):
        header = ["Клиент", "Проект", "География (ближайший крупный город)", "Дата запуска рекламы", "",
                  "Ответственный маркетолог", "Вахта", "Ставки за смену", "Ссылка на ТЗ",
                  "Текущий статус", "Наименование проекта", "Медиаплан"]
        rows = [_mk_row([
            _mk_cell("A"), _mk_cell("B"), _mk_cell(""), _mk_cell(""), _mk_cell(""),
            _mk_cell(""), _mk_cell(""), _mk_cell(""),
            _mk_cell("link", hyperlink="https://docs.google.com/spreadsheets/d/AB/edit"),
            _mk_cell(""), _mk_cell("A B"), _mk_cell(""),
        ])]
        svc = self._stub_service(_mk_sheets_response(header, rows))
        with patch.object(rl, "get_sheets_service", return_value=svc):
            p1 = rl.load_projects_list()
            p2 = rl.load_projects_list()
        assert p1 == p2
        # spreadsheets().get вызвался один раз
        assert svc.spreadsheets.return_value.get.call_count == 1

    def test_cache_expired_refetches(self):
        header = ["Клиент", "Проект", "География (ближайший крупный город)", "Дата запуска рекламы", "",
                  "Ответственный маркетолог", "Вахта", "Ставки за смену", "Ссылка на ТЗ",
                  "Текущий статус", "Наименование проекта", "Медиаплан"]
        rows = [_mk_row([
            _mk_cell("A"), _mk_cell("B"), _mk_cell(""), _mk_cell(""), _mk_cell(""),
            _mk_cell(""), _mk_cell(""), _mk_cell(""),
            _mk_cell("link", hyperlink="https://docs.google.com/spreadsheets/d/AB/edit"),
            _mk_cell(""), _mk_cell("A B"), _mk_cell(""),
        ])]
        svc = self._stub_service(_mk_sheets_response(header, rows))
        with patch.object(rl, "get_sheets_service", return_value=svc):
            rl.load_projects_list()
            # отматываем кэш-таймстамп за пределы TTL
            with rl._cache_lock:
                rl._projects_ts = time.time() - rl.PROJECTS_TTL - 10
            rl.load_projects_list()
        assert svc.spreadsheets.return_value.get.call_count == 2

    def test_handles_missing_link_column(self):
        header = ["A", "B"]  # без "Ссылка на ТЗ"
        rows = [_mk_row([_mk_cell("x"), _mk_cell("y")])]
        with patch.object(rl, "get_sheets_service", return_value=self._stub_service(_mk_sheets_response(header, rows))):
            assert rl.load_projects_list() == {}

    def test_handles_api_exception(self):
        svc = MagicMock()
        svc.spreadsheets.return_value.get.return_value.execute.side_effect = RuntimeError("boom")
        with patch.object(rl, "get_sheets_service", return_value=svc):
            assert rl.load_projects_list() == {}


class TestReadTzBatched:
    """Тесты на новую batch-реализацию read_tz_data."""

    def setup_method(self):
        with rl._cache_lock:
            rl._tz_cache = {}

    def _stub(self, meta_titles, batch_value_ranges):
        svc = MagicMock()
        svc.spreadsheets.return_value.get.return_value.execute.return_value = {
            "sheets": [{"properties": {"title": t}} for t in meta_titles]
        }
        svc.spreadsheets.return_value.values.return_value.batchGet.return_value.execute.return_value = {
            "valueRanges": batch_value_ranges
        }
        return svc

    def test_no_service_returns_warning(self):
        with patch.object(rl, "get_sheets_service", return_value=None):
            out = rl.read_tz_data("X")
        assert out.startswith("⚠️")

    def test_single_call_per_tz_metadata_plus_batch(self):
        svc = self._stub(
            ["ЗАЯВКА", "Комплектовщик"],
            [
                {"range": "ЗАЯВКА", "values": [["Должность", "Кол-во"], ["Комплектовщик", "5"]]},
                {"range": "Комплектовщик", "values": [["Ставка", "2500"]]},
            ],
        )
        with patch.object(rl, "get_sheets_service", return_value=svc):
            out = rl.read_tz_data("TZ_ID_1")
        # ровно один metadata-get и один batchGet — N+1 устранён
        assert svc.spreadsheets.return_value.get.call_count == 1
        assert svc.spreadsheets.return_value.values.return_value.batchGet.call_count == 1
        assert "=== Лист: ЗАЯВКА ===" in out
        assert "Должность | Кол-во" in out
        assert "=== Лист: Комплектовщик ===" in out
        assert "Ставка | 2500" in out

    def test_empty_sheet_skipped(self):
        svc = self._stub(
            ["A", "B"],
            [{"range": "A", "values": [["x"]]}, {"range": "B"}],
        )
        with patch.object(rl, "get_sheets_service", return_value=svc):
            out = rl.read_tz_data("TZ_ID_2")
        assert "=== Лист: A ===" in out
        assert "=== Лист: B ===" not in out

    def test_cache_prevents_second_api_call(self):
        svc = self._stub(["S"], [{"range": "S", "values": [["v"]]}])
        with patch.object(rl, "get_sheets_service", return_value=svc):
            rl.read_tz_data("CACHED")
            rl.read_tz_data("CACHED")
        # повторный вызов не должен идти в API
        assert svc.spreadsheets.return_value.get.call_count == 1

    def test_api_exception_returns_error_string(self):
        svc = MagicMock()
        svc.spreadsheets.return_value.get.return_value.execute.side_effect = RuntimeError("nope")
        with patch.object(rl, "get_sheets_service", return_value=svc):
            out = rl.read_tz_data("BAD")
        assert out.startswith("Ошибка чтения ТЗ")


class TestWarmup:
    def setup_method(self):
        rl._warmup_started = False
        with rl._cache_lock:
            rl._projects_cache = None
            rl._handbook_cache = None
            rl._tz_cache = {}

    def test_warmup_calls_loaders_and_top_tz(self):
        # имитируем уже прогретый список — load_projects_list вернёт что есть
        with patch.object(rl, "load_projects_list", return_value={
            "P1": {"tz_id": "TZ1"},
            "P2": {"tz_id": "TZ2"},
            "P3": {"tz_id": ""},
        }) as m_proj, patch.object(rl, "load_handbook", return_value="hb") as m_hb, \
                patch.object(rl, "read_tz_data", return_value="data") as m_tz:
            rl.warmup_async(top_n_tz=5)
            # ждём окончания потока
            import threading
            for t in threading.enumerate():
                if t.name == "recruiter-warmup":
                    t.join(timeout=5)
        m_proj.assert_called_once()
        m_hb.assert_called_once()
        # TZ3 пропускается из-за пустого tz_id
        called_ids = [c.args[0] for c in m_tz.call_args_list]
        assert called_ids == ["TZ1", "TZ2"]

    def test_warmup_idempotent(self):
        with patch.object(rl, "load_projects_list", return_value={}) as m_proj, \
                patch.object(rl, "load_handbook", return_value="") as m_hb:
            rl.warmup_async()
            rl.warmup_async()  # второй вызов не должен запускать снова
            import threading
            for t in threading.enumerate():
                if t.name == "recruiter-warmup":
                    t.join(timeout=5)
        # load_projects_list должен быть вызван ровно один раз
        assert m_proj.call_count == 1
