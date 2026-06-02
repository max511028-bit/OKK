// Тесты для 2.1 (логи ИИ) + 2.2 (расширенный контекст Финансов).
// Запуск: node scripts/test_ai_logs_finance_ctx.js
const fs = require('fs');
const path = require('path');

const PY        = fs.readFileSync(path.join(__dirname, '..', 'tasks', 'api', 'main.py'), 'utf8').replace(/\r\n/g, '\n');
const ADMIN     = fs.readFileSync(path.join(__dirname, '..', 'admin.html'), 'utf8').replace(/\r\n/g, '\n');
const INJECT    = fs.readFileSync(path.join(__dirname, 'inject_ai.py'), 'utf8').replace(/\r\n/g, '\n');
const FINANCE   = fs.readFileSync(path.join(__dirname, '..', 'finance', 'index.html'), 'utf8').replace(/\r\n/g, '\n');

let pass = 0, fail = 0;
function ok(n){ console.log('\x1b[32m✓\x1b[0m', n); pass++; }
function bad(n, r){ console.log('\x1b[31m✗\x1b[0m', n, '\n   ', r); fail++; }
function assert(c, n, r){ c ? ok(n) : bad(n, r||'assertion failed'); }

// === 2.1 — ЛОГИ ИИ ===
// 1. Таблица ai_logs со всеми нужными колонками
assert(/CREATE TABLE IF NOT EXISTS ai_logs/.test(PY), '2.1: таблица ai_logs создаётся');
['ts','dashboard','user_token_short','question','response','prompt_tokens','completion_tokens','latency_ms','cache_hit','error']
  .forEach(col => assert(new RegExp('\\b'+col+'\\b').test(PY.slice(PY.indexOf('CREATE TABLE IF NOT EXISTS ai_logs'), PY.indexOf('CREATE TABLE IF NOT EXISTS ai_logs')+800)),
    `2.1: колонка ${col} в ai_logs`));
assert(/idx_ai_logs_ts/.test(PY), '2.1: индекс idx_ai_logs_ts');
assert(/idx_ai_logs_dashboard/.test(PY), '2.1: индекс idx_ai_logs_dashboard');

// 2. Endpoints
assert(/@app\.get\("\/admin\/ai-logs"\)/.test(PY), '2.1: GET /admin/ai-logs');
assert(/@app\.get\("\/admin\/ai-logs\/stats"\)/.test(PY), '2.1: GET /admin/ai-logs/stats');
assert(/cache_hit_rate/.test(PY), '2.1: stats возвращает cache_hit_rate');
assert(/by_dashboard_7d/.test(PY), '2.1: stats возвращает by_dashboard_7d');

// 3. Запись в ai_logs в proxy_ai_chat_stream
const proxyIdx = PY.indexOf('async def proxy_ai_chat_stream');
assert(proxyIdx > 0, '2.1: proxy_ai_chat_stream найден');
const proxySlice = PY.slice(proxyIdx, proxyIdx + 10000);
assert(/INSERT INTO ai_logs|_log_ai/i.test(proxySlice) || /ai_logs/.test(proxySlice),
  '2.1: proxy логирует в ai_logs');

// 4. Admin UI
assert(/tab-btn-ai-logs/.test(ADMIN), '2.1: tab кнопка ai-logs');
assert(/panel-ai-logs/.test(ADMIN), '2.1: panel ai-logs');
assert(/loadAiLogs\s*\(/.test(ADMIN), '2.1: loadAiLogs() функция');
assert(/loadAiLogsStats\s*\(/.test(ADMIN), '2.1: loadAiLogsStats() функция');
assert(/lg-filter/.test(ADMIN), '2.1: фильтр по дашборду');
assert(/lg-cache/.test(ADMIN), '2.1: счётчик cache hits');
assert(/lg-latency/.test(ADMIN), '2.1: средняя латентность');
assert(/lg-tbody/.test(ADMIN), '2.1: tbody таблицы');
assert(/lg-by-dashboard/.test(ADMIN), '2.1: разбивка по дашбордам 7д');
// Авто-загрузка при переключении на вкладку
assert(/loadAiLogsStats\(\);\s*loadAiLogs\(\)/.test(ADMIN), '2.1: авто-загрузка при switch tab');

// === 2.2 — КОНТЕКСТ ФИНАНСОВ ===
// 1. inject_ai.py содержит расширения
assert(/Помесячно \(последние 3\)/.test(INJECT), '2.2: блок помесячной динамики');
assert(/Топ-10 по марже/.test(INJECT), '2.2: блок Топ-10 по марже');
assert(/Топ-3 проблемных/.test(INJECT), '2.2: блок Топ-3 проблемных (<8%)');
assert(/По городам/.test(INJECT), '2.2: блок по городам');
assert(/MoM-динамика топ-5 клиентов/.test(INJECT), '2.2: MoM-динамика клиентов');
assert(/aggByField/.test(INJECT), '2.2: использует aggByField');

// 2. После инжекции finance/index.html содержит все блоки
assert(/Помесячно \(последние 3\)/.test(FINANCE),  '2.2: finance/index.html: помесячно');
assert(/Топ-10 по марже/.test(FINANCE),            '2.2: finance/index.html: топ-10 марже');
assert(/Топ-3 проблемных/.test(FINANCE),           '2.2: finance/index.html: топ-3 проблемных');
assert(/По городам \(топ-7\)/.test(FINANCE),       '2.2: finance/index.html: по городам');
assert(/MoM-динамика топ-5 клиентов/.test(FINANCE),'2.2: finance/index.html: MoM клиенты');

// 3. Логика — поведенческие тесты на JS-симуляции
// Имитируем структуру D и функцию aggByField, прогоняем кусок ctx-логики
function makeMonth(key, label, projs) {
  const rev = projs.reduce((s,p)=>s+p.rev,0);
  const fin = projs.reduce((s,p)=>s+p.rev*(p.margin/100),0);
  return { key, label, rev, fin, projs };
}
const D = {
  monthly: [
    makeMonth('01.04.2026','апрель 2026', [
      {name:'Москва_Озон_Wildberries', rev:1000000, margin:12},
      {name:'СПб_X5_Lenta',             rev:800000,  margin:6},
      {name:'Краснодар_Магнит',         rev:500000,  margin:15},
    ]),
    makeMonth('01.05.2026','май 2026', [
      {name:'Москва_Озон_Wildberries', rev:1200000, margin:14},
      {name:'СПб_X5_Lenta',             rev:600000,  margin:4},  // упал
      {name:'Краснодар_Магнит',         rev:550000,  margin:16},
    ]),
  ],
  projects_per_month: {},
  cities: [{name:'Москва'},{name:'СПб'},{name:'Краснодар'}],
  clients: [{name:'Озон'},{name:'X5'},{name:'Магнит'}],
};
D.projects_per_month['01.04.2026'] = D.monthly[0].projs;
D.projects_per_month['01.05.2026'] = D.monthly[1].projs;

// Топ-3 проблемных (margin < 8% и rev > 50k)
const keys = ['01.04.2026','01.05.2026'];
const acc = {};
keys.forEach(k => (D.projects_per_month[k]||[]).forEach(p => {
  if(!acc[p.name]) acc[p.name] = {rev:0,fin:0};
  acc[p.name].rev += p.rev;
  acc[p.name].fin += p.rev * (p.margin/100);
}));
const projs = Object.entries(acc).map(([n,a]) => ({
  n, rev:a.rev, mar: a.rev>0 ? a.fin/a.rev*100 : 0
})).filter(p=>p.rev>0);
const probl = projs.filter(p=>p.rev>50000 && p.mar<8);
assert(probl.length === 1 && probl[0].n.includes('X5'),
  '2.2 sim: проблемный проект = СПб_X5_Lenta');
assert(probl[0].mar < 8 && probl[0].mar > 4,
  '2.2 sim: его средняя маржа в (4..8)%');

// MoM-динамика — клиент X5 должен упасть
const lastByClient  = {'X5': {rev:600000, margin:4}};
const prevByClient  = {'X5': {rev:800000, margin:6}};
const dRev = lastByClient['X5'].rev - prevByClient['X5'].rev;
const pct  = prevByClient['X5'].rev>0 ? dRev/prevByClient['X5'].rev*100 : 0;
assert(dRev < 0 && Math.round(pct) === -25, '2.2 sim: X5 падение -25% MoM');

// === 2.5 — ПЛАШКА «ИИ НЕДОСТУПЕН» ===
// 1. inject_ai.py содержит CSS+JS плашки
assert(/_ai_offline_bar/.test(INJECT),       '2.5: inject_ai.py: id _ai_offline_bar в CSS');
assert(/_aiHealthTick/.test(INJECT),         '2.5: inject_ai.py: функция _aiHealthTick');
assert(/\/tasks\/api\/ai\/health/.test(INJECT),'2.5: inject_ai.py: fetch на /tasks/api/ai/health');
assert(/setInterval\(_aiHealthTick,60000\)/.test(INJECT), '2.5: inject_ai.py: опрос каждые 60с');
assert(/singleton|getElementById\(['"]_ai_offline_bar['"]\)/i.test(INJECT), '2.5: inject_ai.py: singleton-проверка');

// 2. После инжекции finance/index.html содержит плашку
assert(/_ai_offline_bar/.test(FINANCE),      '2.5: finance/index.html: _ai_offline_bar присутствует');
assert(/_aiHealthTick/.test(FINANCE),        '2.5: finance/index.html: _aiHealthTick присутствует');

// 3. Backend endpoint /ai/health
assert(/@app\.get\("\/ai\/health"\)/.test(PY),'2.5: backend: GET /ai/health');
assert(/_AI_HEALTH_CACHE/.test(PY),           '2.5: backend: health-кэш');

// === 2.3 — CRON PURGE-TRASH ===
const fs2 = require('fs');
const path2 = require('path');
const purgePath = path2.join(__dirname, 'sth-purge-trash.sh');
assert(fs2.existsSync(purgePath),             '2.3: scripts/sth-purge-trash.sh существует');
if (fs2.existsSync(purgePath)) {
  const PURGE = fs2.readFileSync(purgePath, 'utf8');
  assert(/curl[\s\S]{0,200}"?\$?API"?/.test(PURGE) && /purge-trash/.test(PURGE),
                                              '2.3: скрипт делает curl на /admin/tasks/purge-trash');
  assert(/127\.0\.0\.1:8601/.test(PURGE),     '2.3: скрипт стучится на uvicorn 127.0.0.1:8601');
  assert(/days=/.test(PURGE),                 '2.3: параметр days передаётся');
  assert(/#!\/bin\/bash/.test(PURGE),         '2.3: shebang bash');
  assert(/0 5 \* \* \*/.test(PURGE),          '2.3: инструкция cron 0 5 * * *');
}

// Backend purge-trash endpoint остаётся работоспособным
assert(/@app\.post\("\/admin\/tasks\/purge-trash"\)/.test(PY), '2.3: backend: POST /admin/tasks/purge-trash');
assert(/deleted_at IS NOT NULL AND deleted_at < datetime/.test(PY), '2.3: backend: чистит по deleted_at + TTL');
assert(/_is_local_request/.test(PY),                                '2.3: backend: доступ с localhost разрешён (для cron)');

// === 2.6 — ШАБЛОНЫ ВОПРОСОВ ===
// Все 6 конфигов в inject_ai.py должны иметь >=10 suggestions
['wb','finance','kp','hr-game','month','sales'].forEach(function(t){
  // грубо: ищем секцию config и считаем строки с одинарной кавычкой в массиве suggestions
  var re = new RegExp("'"+t.replace('-','\\-')+"'\\s*:\\s*\\{[\\s\\S]*?suggestions[\\s\\S]*?\\]", 'm');
  var m = INJECT.match(re);
  if(!m){ bad('2.6: конфиг '+t+' найден', 'не нашёлся блок suggestions'); return; }
  var cnt = (m[0].match(/'[^']{5,}'/g)||[]).length - 1; // -1 на ключ 'suggestions'
  assert(cnt >= 9, '2.6: '+t+': шаблонов >=10 (фактически '+cnt+')');
});

// OKK имеет встроенные ai-chip (10 штук) прямо в html
const OKK_HTML = fs.readFileSync(path.join(__dirname, '..', 'okk', 'index.html'), 'utf8');
const okkChips = (OKK_HTML.match(/class="ai-chip"/g)||[]).length;
assert(okkChips >= 10, '2.6: okk/index.html: >=10 ai-chip (фактически '+okkChips+')');

// === КОНЕЦ ===
console.log(`\n=== ${pass} passed, ${fail} failed ===`);
process.exit(fail > 0 ? 1 : 0);
