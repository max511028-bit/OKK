// Сверка: «эталон из CSV» vs «то что вытащил парсер дашборда»
// Запускается так же как test_parsers.js — внутри стабит браузерные API,
// eval'ит inline-скрипт из okk/index.html, прогоняет валидаторы.

const fs = require('fs');
const https = require('https');
const path = require('path');
const candidates = [
  'okk/index.html',
  'index.html',
  path.join(__dirname, 'index.html'),
  path.join(__dirname, '..', 'okk', 'index.html'),
  path.join(__dirname, 'okk', 'index.html'),
];
const htmlPath = candidates.find(p => fs.existsSync(p));
if (!htmlPath) { console.error('okk/index.html не найден. Искал:', candidates); process.exit(2); }
const html = fs.readFileSync(htmlPath, 'utf8');

const re = /<script\b[^>]*>([\s\S]*?)<\/script>/g;
let m, code = '';
while ((m = re.exec(html))) {
  const tagStart = html.lastIndexOf('<script', m.index);
  const tag = html.substring(tagStart, m.index);
  if (tag.includes('src=')) continue;
  code += '\n' + m[1];
}
function makeStub() {
  return new Proxy(function(){}, {
    get(t,p) { if (p === Symbol.toPrimitive) return ()=>'0'; if (p==='length') return 0; return makeStub(); },
    set() { return true; }, apply() { return makeStub(); }, has() { return true; },
  });
}
global.document = makeStub();
global.window = makeStub();
global.localStorage = { getItem:()=>null, setItem:()=>{}, removeItem:()=>{} };
global.fetch = ()=>Promise.reject(new Error('no fetch'));
global.Chart = function(){ this.destroy=()=>{}; };
global.Chart.defaults = makeStub();
global.Chart.register = ()=>{};
global.Chart.Tooltip = makeStub();
global.URL = require('url').URL;
global.location = { reload: ()=>{} };

code = code.replace(/\binitDashboard\(\);?/g, '/*skip*/');
try { eval(`(function(){ ${code} ; globalThis.__P={parseCSV,parseMPPSheet,parseORPSheet,parseSurveySheet,parseTrainingSheet,parseRawCallsTab,parseCallsSheetMonthly,monthFromDate,ALL_MONTHS,CRITERIA_NAMES:typeof CRITERIA_NAMES!=='undefined'?CRITERIA_NAMES:[],parsePercent,parseNum}; })();`); }
catch(e) { console.error('eval err', e.message); process.exit(1); }
const P = global.__P;

function gviz(sid, sheet) {
  const url = `https://docs.google.com/spreadsheets/d/${sid}/gviz/tq?tqx=out:csv&sheet=${encodeURIComponent(sheet)}`;
  return new Promise((resolve, reject) => {
    https.get(url, res => {
      let data = ''; res.on('data', c => data += c); res.on('end', () => resolve(data));
    }).on('error', reject);
  });
}

// Цветовая разметка для отчёта
const OK = (s) => `\x1b[32m✓\x1b[0m ${s}`;
const WARN = (s) => `\x1b[33m⚠\x1b[0m ${s}`;
const ERR = (s) => `\x1b[31m✗\x1b[0m ${s}`;

const report = []; // {file, tab, check, expected, actual, status, note}
function add(file, tab, check, expected, actual, status, note='') {
  report.push({file, tab, check, expected, actual, status, note});
  const label = `[${file}/${tab}] ${check}: csv=${expected} parser=${actual} ${note}`;
  console.log(status === 'ok' ? OK(label) : status === 'warn' ? WARN(label) : ERR(label));
}

async function checkRawTab(sid, file, tab, monthLabel) {
  const csv = await gviz(sid, tab);
  const rows = P.parseCSV(csv);
  // Эталон: уникальные ФИО в колонке 0 (без заголовка, без Итого)
  const fios = new Set();
  let totalCalls = 0;
  for (let i = 1; i < rows.length; i++) {
    const name = (rows[i][0]||'').trim();
    if (!name || /^Итого|Среднее|Аналитика/i.test(name)) continue;
    fios.add(name); totalCalls++;
  }
  const recs = P.parseRawCallsTab(rows, monthLabel);
  const parserCalls = recs.reduce((s,r)=>s+r.calls, 0);
  add(file, tab, 'unique_recruiters', fios.size, recs.length,
      fios.size === recs.length ? 'ok' : 'warn');
  add(file, tab, 'total_calls', totalCalls, parserCalls,
      totalCalls === parserCalls ? 'ok' : 'warn');
}

async function checkORP(sid, file, tab) {
  const csv = await gviz(sid, tab);
  const rows = P.parseCSV(csv);
  // Эталон: количество строк с непустым ФИО + распознаваемой датой
  let validCsv = 0, badDates = 0;
  for (let i = 1; i < rows.length; i++) {
    const row = rows[i]; if (!row) continue;
    // Найти дату (может быть в col 0 или 1)
    const d0 = P.monthFromDate(row[0]||''), d1 = P.monthFromDate(row[1]||'');
    const hasDate = d0 || d1;
    const hasName = (row[1]||row[2]||row[3]||'').trim().length > 3 && /[А-Яа-яA-Za-z]/.test(row[1]||row[2]||row[3]||'');
    if (hasDate && hasName) validCsv++;
    else if (hasName && !hasDate && (row[0]||row[1]||'').match(/\d{1,2}[\.\/-]\d/)) badDates++;
  }
  const recs = P.parseORPSheet(rows);
  add(file, tab, 'records', validCsv, recs.length,
      Math.abs(validCsv - recs.length) <= 2 ? 'ok' : 'warn',
      badDates ? `(битых дат в CSV: ${badDates})` : '');
  const withDiv = recs.filter(x=>x.division).length;
  const pct = recs.length ? (withDiv/recs.length*100).toFixed(0) : 0;
  add(file, tab, 'with_division', '?', `${withDiv}/${recs.length} (${pct}%)`,
      pct >= 80 ? 'ok' : 'warn');
}

async function checkSurvey(sid, file, tab, year) {
  const csv = await gviz(sid, tab);
  const rows = P.parseCSV(csv);
  // Эталон: ненулевые data-строки
  let dataRows = 0;
  for (let i = 1; i < rows.length; i++) {
    const r = rows[i]; if (!r) continue;
    if ((r[0]||'').trim() && r.slice(1).some(c => (c||'').trim())) dataRows++;
  }
  const s = P.parseSurveySheet(rows, year);
  add(file, tab, 'records_extracted', `~${dataRows}`, s.records.length,
      s.records.length > 0 ? 'ok' : 'error');
  add(file, tab, 'response_rates', '?', s.responseRates.length, 'ok');
}

async function checkTraining(sid, file, tab) {
  const csv = await gviz(sid, tab);
  const rows = P.parseCSV(csv);
  // Эталон: непустые ФИО в одной из первых 4 колонок
  let fioRows = 0;
  for (let i = 1; i < rows.length; i++) {
    const r = rows[i]; if (!r) continue;
    for (let k = 0; k < 4; k++) {
      const v = (r[k]||'').trim();
      if (v.length > 5 && /[А-Яа-я]+\s+[А-Яа-я]/.test(v)) { fioRows++; break; }
    }
  }
  const t = P.parseTrainingSheet(rows);
  const got = t.squeeze.length + t.lmk.length;
  add(file, tab, 'training_records', `~${fioRows}`, got,
      got > 0 ? 'ok' : 'error', `squeeze=${t.squeeze.length} lmk=${t.lmk.length}`);
}

async function main() {
  console.log('\n=== СВЕРКА: что в Google-таблицах vs что вытащил дашборд ===\n');

  // KЦ — Тех. листы (сырые звонки)
  console.log('--- 2. Статистика КЦ → Тех. листы ---');
  await checkRawTab('1vxEUq2m92T3oPl25vuxKUxTDDZO5hZJHZlC_88xR-8w', 'KЦ', 'Тех. лист 03.26', 'МАРТ 2026');
  await checkRawTab('1vxEUq2m92T3oPl25vuxKUxTDDZO5hZJHZlC_88xR-8w', 'KЦ', 'Тех. лист 02.26', 'ФЕВРАЛЬ 2026');
  await checkRawTab('1vxEUq2m92T3oPl25vuxKUxTDDZO5hZJHZlC_88xR-8w', 'KЦ', 'Тех. лист 01.26', 'ЯНВАРЬ 2026');

  console.log('\n--- 2. Статистика КЦ → Обучение/Метрики ---');
  await checkTraining('1i94OV_2e9B5CGIYBFkWN3W8SLfT8R93598dyoYEoe08', 'KЦ', 'Обучение по выгодам 2025');
  await checkTraining('1i94OV_2e9B5CGIYBFkWN3W8SLfT8R93598dyoYEoe08', 'KЦ', 'Обучение возражение ЛМК 2026');
  await checkTraining('1i94OV_2e9B5CGIYBFkWN3W8SLfT8R93598dyoYEoe08', 'KЦ', 'Метрики 2025');

  console.log('\n--- 4. Тайные ОРП ---');
  await checkORP('1dV5tAXBbs7DLSTw1PBUaW800NvUm9W7TvLe4O4yogy0', 'ОРП', 'Сводная 2026');
  await checkORP('1dV5tAXBbs7DLSTw1PBUaW800NvUm9W7TvLe4O4yogy0', 'ОРП', 'Сводная 2025');

  console.log('\n--- 5. Опросы РП ---');
  await checkSurvey('13eIDsffRUYRyW6Df6-W4dpUzxMehM-yqInOTTnIfZnE', 'Опросы', 'Сводные новый РП 2026', '2026');
  await checkSurvey('13eIDsffRUYRyW6Df6-W4dpUzxMehM-yqInOTTnIfZnE', 'Опросы', 'Сводные новый РП 2025', '2025');

  // Итоги
  const ok = report.filter(r=>r.status==='ok').length;
  const warn = report.filter(r=>r.status==='warn').length;
  const err = report.filter(r=>r.status==='error').length;
  console.log(`\n=== ИТОГ: ${OK(ok)} ${WARN(warn)} ${ERR(err)} ===`);

  // Сохранить JSON-отчёт
  const ts = new Date().toISOString();
  fs.writeFileSync('validation-report.json', JSON.stringify({timestamp: ts, summary: {ok, warn, err}, checks: report}, null, 2));
  console.log('Отчёт: validation-report.json');
  process.exit(err > 0 ? 1 : 0);
}
main().catch(e=>{console.error(e); process.exit(1);});
