// Тесты для 4.3 — кэш ответов ИИ.
// Запуск: node scripts/test_ai_cache.js
const fs = require('fs');
const path = require('path');

const PY      = fs.readFileSync(path.join(__dirname, '..', 'tasks', 'api', 'main.py'), 'utf8').replace(/\r\n/g, '\n');
const ADMIN   = fs.readFileSync(path.join(__dirname, '..', 'admin.html'), 'utf8').replace(/\r\n/g, '\n');
const CRON    = fs.readFileSync(path.join(__dirname, 'sth-ai-cache-cleanup.sh'), 'utf8');

let pass = 0, fail = 0;
function ok(n){ console.log('\x1b[32m✓\x1b[0m', n); pass++; }
function bad(n, r){ console.log('\x1b[31m✗\x1b[0m', n, '\n   ', r); fail++; }
function assert(c, n, r){ c ? ok(n) : bad(n, r||'assertion failed'); }

// === Схема ===
assert(/CREATE TABLE IF NOT EXISTS ai_cache/.test(PY), '4.3: таблица ai_cache создаётся');
['key','dashboard','question','response','created_at','hit_count'].forEach(col => {
  const sl = PY.slice(PY.indexOf('CREATE TABLE IF NOT EXISTS ai_cache'),
                      PY.indexOf('CREATE TABLE IF NOT EXISTS ai_cache')+500);
  assert(new RegExp('\\b'+col+'\\b').test(sl), `4.3: колонка ${col} в ai_cache`);
});
assert(/idx_ai_cache_created/.test(PY),   '4.3: индекс idx_ai_cache_created');
assert(/idx_ai_cache_dashboard/.test(PY), '4.3: индекс idx_ai_cache_dashboard');

// === Helper функции ===
assert(/def _ai_cache_key\(/.test(PY),       '4.3: _ai_cache_key()');
assert(/def _ai_cache_get\(/.test(PY),       '4.3: _ai_cache_get()');
assert(/def _ai_cache_put\(/.test(PY),       '4.3: _ai_cache_put()');
assert(/def _ai_cache_stream_replay\(/.test(PY), '4.3: _ai_cache_stream_replay()');
assert(/hashlib\.sha256/.test(PY),           '4.3: используется sha256');
assert(/_AI_CACHE_TTL_HOURS\s*=\s*24/.test(PY), '4.3: TTL = 24 часа');

// === Логика встроена в proxy_ai_chat_stream ===
const proxyIdx = PY.indexOf('async def proxy_ai_chat_stream');
const proxySlice = PY.slice(proxyIdx, proxyIdx + 15000);
assert(/_ai_cache_get\(cache_key\)/.test(proxySlice), '4.3: proxy делает _ai_cache_get');
assert(/_ai_cache_put\(cache_key/.test(proxySlice),   '4.3: proxy делает _ai_cache_put');
assert(/X-Cache.*HIT/.test(proxySlice),               '4.3: при hit header X-Cache: HIT');
assert(/cache_hit=True/.test(proxySlice),             '4.3: при hit пишет cache_hit=True в ai_logs');
assert(/not client_aborted/.test(proxySlice),         '4.3: не кэшируем если клиент отвалился');

// === Endpoints ===
assert(/@app\.get\("\/admin\/ai-cache\/stats"\)/.test(PY),  '4.3: GET /admin/ai-cache/stats');
assert(/@app\.post\("\/admin\/ai-cache\/clear"\)/.test(PY), '4.3: POST /admin/ai-cache/clear');
assert(/top_questions/.test(PY),                            '4.3: stats возвращает top_questions');
assert(/hit_rate_7d_pct/.test(PY),                          '4.3: stats возвращает hit_rate_7d_pct');

// === Admin UI ===
assert(/lc-total/.test(ADMIN),       '4.3: admin: счётчик всего записей');
assert(/lc-fresh/.test(ADMIN),       '4.3: admin: счётчик свежих');
assert(/lc-hits/.test(ADMIN),        '4.3: admin: счётчик попаданий');
assert(/lc-hitrate/.test(ADMIN),     '4.3: admin: hit-rate');
assert(/lc-top/.test(ADMIN),         '4.3: admin: топ-вопросов');
assert(/loadAiCacheStats/.test(ADMIN), '4.3: admin: функция loadAiCacheStats');
assert(/clearAiCache/.test(ADMIN),     '4.3: admin: функция clearAiCache');
assert(/loadAiLogsStats\(\);\s*loadAiLogs\(\);\s*loadAiCacheStats\(\)/.test(ADMIN),
  '4.3: admin: автозагрузка cache stats при switch tab');

// === Cron ===
assert(/sqlite3.*ai_cache.*7 day/i.test(CRON),    '4.3: cron чистит ai_cache >7 дней');
assert(/VACUUM/i.test(CRON),                       '4.3: cron делает VACUUM');
assert(/0 4 \* \* 0/.test(CRON),                   '4.3: cron-инструкция 0 4 * * 0 (вс 04:00)');

// === Поведенческая логика ключа (симуляция на JS) ===
function jsKey(dashboard, question, sysText){
  const crypto = require('crypto');
  const qNorm = (question||'').toLowerCase().trim().split(/\s+/).join(' ');
  const ctx = crypto.createHash('sha256').update(sysText||'','utf8').digest('hex');
  return crypto.createHash('sha256').update(`${dashboard}\x00${qNorm}\x00${ctx}`,'utf8').digest('hex');
}
// Один и тот же вопрос+данные → одинаковый ключ
const k1 = jsKey('finance', 'Топ-3 проекта по марже?', 'данные A=100');
const k2 = jsKey('finance', 'Топ-3 проекта по марже?', 'данные A=100');
assert(k1 === k2, '4.3 sim: одинаковый вопрос+данные → одинаковый ключ');
// Изменились данные → ключ другой
const k3 = jsKey('finance', 'Топ-3 проекта по марже?', 'данные A=200');
assert(k1 !== k3, '4.3 sim: цифры изменились → ключ другой (cache miss)');
// Изменился вопрос → ключ другой
const k4 = jsKey('finance', 'Топ-5 проектов по марже?', 'данные A=100');
assert(k1 !== k4, '4.3 sim: вопрос изменился → ключ другой');
// Изменился дашборд → ключ другой
const k5 = jsKey('wb', 'Топ-3 проекта по марже?', 'данные A=100');
assert(k1 !== k5, '4.3 sim: дашборд другой → ключ другой');
// Нормализация регистра/пробелов
const k6 = jsKey('finance', '  ТОП-3 проекта по марже?  ', 'данные A=100');
assert(k1 === k6, '4.3 sim: нормализация (case + пробелы) — ключи равны');

console.log(`\n=== ${pass} passed, ${fail} failed ===`);
process.exit(fail > 0 ? 1 : 0);
