"""Обновление month/seed/monthly-reports.json для МАРТА и АПРЕЛЯ 2026 из Google Sheets.

Источник: https://docs.google.com/spreadsheets/d/1HJ5pxW-MNDZ71H6m6-uu6oJR9cy_A5GUfUEVWpqTJUg/edit
Вкладки: МАРТ (gid=0), АПРЕЛЬ (gid=177346913).

Делает:
1. Парсит CSV-экспорты обоих листов.
2. Обновляет метрики и комментарии каждого проекта в reports[2026-03] и reports[2026-04].
3. Для новых проектов, отсутствующих в JSON, добавляет запись и в projects[] registry, и в reports.
4. Для апрельских колонок «Средний выход по BI» и «Отклонения» добавляет поля avgOutputBI и avgOutputDeviation.
5. Никакие другие месяцы не трогает.

Запуск: python scripts/update_month_reports_from_sheets.py
"""
import csv, json, sys, os, re
sys.stdout.reconfigure(encoding='utf-8')

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SEED_PATH = os.path.join(ROOT, 'month', 'seed', 'monthly-reports.json')
MARCH_CSV = 'C:/tmp/march.csv'
APRIL_CSV = 'C:/tmp/april.csv'


def num(s):
    if s is None: return None
    s = str(s).strip()
    if not s or s in ('-', '—', '#ДЕЛ/0!', '#DIV/0!', '#REF!', '#N/A', '#VALUE!'): return None
    s = s.replace('\xa0', '').replace(' ', '').replace('р.', '').replace('₽', '')
    if s.endswith('%'):
        s = s[:-1].replace(',', '.')
        try: return float(s) / 100
        except ValueError: return None
    s = s.replace(',', '.')
    try: return float(s)
    except ValueError: return None


def txt(s):
    if s is None: return None
    s = str(s).strip()
    if not s or s in ('-', '—'): return None
    return s


def parse_csv(path, has_bi_cols):
    with open(path, 'r', encoding='utf-8') as f:
        rows = list(csv.reader(f))
    data = rows[2:]
    out = []
    current_div = None
    for r in data:
        if not any(c.strip() for c in r): continue
        div_raw = r[0].strip()
        proj = r[1].strip()
        # Skip totals / metadata строки (могут быть в колонке "Дивизион" или "Проект")
        skip_markers = ('количество', 'итого', 'динамика', 'сумма ', '(+', '(-')
        if div_raw and any(div_raw.lower().startswith(m) for m in skip_markers):
            continue
        if proj and any(proj.lower().startswith(m) for m in skip_markers):
            continue
        if div_raw:
            current_div = div_raw
        if not proj: continue
        # Если в колонке "Проект" просто число (например, счётчик проектов) — пропустить
        if re.fullmatch(r'\d+', proj):
            continue
        c = lambda i: r[i] if i < len(r) else ''
        if has_bi_cols:
            idx = dict(
                request=2, avgOutput=3, avgOutputBI=4, avgOutputDeviation=5,
                currentStaff=6, productivity=7, penalties=8, penaltyShare=9,
                revenueVat=10, requestCloseRate=11, secretCheck=12,
                realizationPayroll=13, realizDrivers=14, realizBlockers=15,
                recruitingPayroll=16, invited=17, invitedToResponseRate=18,
                registered=19, registeredToInvitedRate=20,
                warehouseReached=21, warehouseToRegisteredRate=22,
                firstShift=23, firstShiftToWarehouseRate=24,
                tenShifts=25, tenShiftsToFirstShiftRate=26,
                recrDrivers=27, recrBlockers=28, responseToWarehouseRate=29,
                marketingProjectName=30, marketingPayroll=31, marketingBudget=32,
                responses=33, targetLeads=34, targetLeadRate=35,
                mktDrivers=36, mktBlockers=37, responseCost=38, targetLeadCost=39,
                agreements=40
            )
        else:
            idx = dict(
                request=2, avgOutput=3, currentStaff=4, productivity=5,
                penalties=6, penaltyShare=7, revenueVat=8, requestCloseRate=9,
                secretCheck=10, realizationPayroll=11, realizDrivers=12, realizBlockers=13,
                recruitingPayroll=14, invited=15, invitedToResponseRate=16,
                registered=17, registeredToInvitedRate=18,
                warehouseReached=19, warehouseToRegisteredRate=20,
                firstShift=21, firstShiftToWarehouseRate=22,
                tenShifts=23, tenShiftsToFirstShiftRate=24,
                recrDrivers=25, recrBlockers=26, responseToWarehouseRate=27,
                marketingProjectName=28, marketingPayroll=29, marketingBudget=30,
                responses=31, targetLeads=32, targetLeadRate=33,
                mktDrivers=34, mktBlockers=35, responseCost=36, targetLeadCost=37,
                agreements=38
            )
        rec = {'division': current_div, 'project': proj}
        text_keys = {'realizDrivers', 'realizBlockers', 'recrDrivers', 'recrBlockers',
                     'mktDrivers', 'mktBlockers', 'agreements', 'marketingProjectName'}
        for k, i in idx.items():
            v = c(i).strip() if i < len(r) else ''
            rec[k] = txt(v) if k in text_keys else num(v)
        out.append(rec)
    return out


def slugify(name):
    s = name.lower().strip()
    s = s.replace('ё', 'е')
    s = re.sub(r'[\s/+]+', '-', s)
    s = re.sub(r'[^a-zа-я0-9\-]+', '', s)
    s = re.sub(r'-+', '-', s).strip('-')
    return s


METRIC_KEYS = [
    'request', 'avgOutput', 'avgOutputBI', 'avgOutputDeviation', 'currentStaff',
    'productivity', 'penalties', 'penaltyShare', 'revenueVat', 'requestCloseRate',
    'secretCheck', 'realizationPayroll', 'recruitingPayroll',
    'invited', 'invitedToResponseRate', 'registered', 'registeredToInvitedRate',
    'warehouseReached', 'warehouseToRegisteredRate', 'firstShift',
    'firstShiftToWarehouseRate', 'tenShifts', 'tenShiftsToFirstShiftRate',
    'responseToWarehouseRate', 'marketingProjectName', 'marketingPayroll',
    'marketingBudget', 'responses', 'targetLeads', 'targetLeadRate',
    'responseCost', 'targetLeadCost'
]

COMMENT_MAP = {
    'realizationDrivers': 'realizDrivers',
    'realizationBlockers': 'realizBlockers',
    'recruitingDrivers': 'recrDrivers',
    'recruitingBlockers': 'recrBlockers',
    'marketingDrivers': 'mktDrivers',
    'marketingBlockers': 'mktBlockers',
    'agreements': 'agreements',
}


def update_report(report, csv_data, registry, leaders_map):
    by_name = {p['project']: p for p in csv_data}
    existing_names = {p['name']: p for p in report['projects']}
    updated, added = [], []
    for csv_p in csv_data:
        name = csv_p['project']
        if name in existing_names:
            target = existing_names[name]
        else:
            sid = slugify(name)
            target = {
                'id': sid,
                'name': name,
                'division': csv_p['division'],
                'divisionLeader': leaders_map.get(csv_p['division']),
                'monthId': report['id'],
                'metrics': {},
                'comments': {},
            }
            report['projects'].append(target)
            added.append(name)
            # Добавляем в верхнеуровневый registry если нет
            if not any(rp['id'] == sid for rp in registry):
                registry.append({
                    'id': sid,
                    'name': name,
                    'divisions': [csv_p['division']] if csv_p['division'] else [],
                    'mediaNames': [csv_p['marketingProjectName']] if csv_p.get('marketingProjectName') else [],
                })
        # division и leader приводим к актуальным
        if csv_p['division']:
            target['division'] = csv_p['division']
            if csv_p['division'] in leaders_map:
                target['divisionLeader'] = leaders_map[csv_p['division']]
        target['monthId'] = report['id']
        # metrics: полностью переписываем — иначе будут торчать прошлогодние "хвосты"
        new_metrics = {}
        for k in METRIC_KEYS:
            new_metrics[k] = csv_p.get(k)
        target['metrics'] = new_metrics
        # comments
        new_comments = {}
        for jk, ck in COMMENT_MAP.items():
            new_comments[jk] = csv_p.get(ck)
        target['comments'] = new_comments
        if name in existing_names:
            updated.append(name)
    return updated, added


def main():
    march = parse_csv(MARCH_CSV, has_bi_cols=False)
    april = parse_csv(APRIL_CSV, has_bi_cols=True)

    with open(SEED_PATH, 'r', encoding='utf-8') as f:
        seed = json.load(f)

    leaders_map = seed.get('divisionLeaders', {})
    registry = seed.setdefault('projects', [])

    reports_by_id = {r['id']: r for r in seed['reports']}

    if '2026-03' in reports_by_id:
        u, a = update_report(reports_by_id['2026-03'], march, registry, leaders_map)
        print(f'МАРТ: обновлено {len(u)}, добавлено {len(a)}')
        if a: print('  + добавлены:', a)
    else:
        print('!!! 2026-03 отсутствует в JSON')

    if '2026-04' in reports_by_id:
        u, a = update_report(reports_by_id['2026-04'], april, registry, leaders_map)
        print(f'АПРЕЛЬ: обновлено {len(u)}, добавлено {len(a)}')
        if a: print('  + добавлены:', a)
    else:
        print('!!! 2026-04 отсутствует в JSON')

    # Обновляем generatedAt
    seed['generatedAt'] = '2026-06-03'
    seed['source'] = 'Google Sheets export: https://docs.google.com/spreadsheets/d/1HJ5pxW-MNDZ71H6m6-uu6oJR9cy_A5GUfUEVWpqTJUg/'

    with open(SEED_PATH, 'w', encoding='utf-8') as f:
        json.dump(seed, f, ensure_ascii=False, indent=2)
    print('Сохранено в', SEED_PATH)


if __name__ == '__main__':
    main()
