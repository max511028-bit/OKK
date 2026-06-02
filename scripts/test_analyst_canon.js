// Behavioural tests for analyst canon library (Variant 3: matching + two-pass + admin).
// Запуск: node scripts/test_analyst_canon.js
const fs = require('fs');
const path = require('path');

const PY = fs.readFileSync(path.join(__dirname, '..', 'tasks', 'api', 'main.py'), 'utf8').replace(/\r\n/g, '\n');
const HTML_ADMIN = fs.readFileSync(path.join(__dirname, '..', 'admin.html'), 'utf8').replace(/\r\n/g, '\n');

let pass = 0, fail = 0;
function ok(n){ console.log('\x1b[32m✓\x1b[0m', n); pass++; }
function bad(n, r){ console.log('\x1b[31m✗\x1b[0m', n, '\n   ', r); fail++; }
function assert(c, n, r){ c ? ok(n) : bad(n, r||'assertion failed'); }

// 1. Сторадж и функции
assert(/ANALYST_CANON_FILE\s*=/.test(PY), 'ANALYST_CANON_FILE defined');
assert(/DEFAULT_ANALYST_CANON\s*:\s*list\[dict\]/.test(PY), 'DEFAULT_ANALYST_CANON defined');
assert(/def _load_analyst_canon\(/.test(PY), '_load_analyst_canon defined');
assert(/def _save_analyst_canon\(/.test(PY), '_save_analyst_canon defined');
assert(/def _match_canon\(/.test(PY), '_match_canon defined');
assert(/def _build_canon_block\(/.test(PY), '_build_canon_block defined');
assert(/def _inject_canon_into_messages\(/.test(PY), '_inject_canon_into_messages defined');
assert(/def _normalize_canon_entry\(/.test(PY), '_normalize_canon_entry defined');
assert(/def _derive_canon_via_model\(/.test(PY), 'two-pass _derive_canon_via_model defined');
assert(/def _is_report_request\(/.test(PY), '_is_report_request heuristic defined');
assert(/def _ollama_base_url\(/.test(PY), '_ollama_base_url helper defined');

// 2. Legacy aliases (back-compat для уже задеплоенных тестов/кода)
assert(/_inject_recipes_into_messages\s*=\s*_inject_canon_into_messages/.test(PY), 'legacy alias _inject_recipes_into_messages → canon');
assert(/_load_analyst_recipes\s*=\s*_load_analyst_canon/.test(PY), 'legacy alias _load_analyst_recipes → canon');
assert(/DEFAULT_ANALYST_RECIPES\s*=\s*DEFAULT_ANALYST_CANON/.test(PY), 'legacy alias DEFAULT_ANALYST_RECIPES → CANON');

// 3. Дефолты: 7 записей с canon (не body)
const defaultBlock = PY.match(/DEFAULT_ANALYST_CANON\s*:\s*list\[dict\]\s*=\s*\[([\s\S]*?)\n\]\n\n\ndef /);
assert(defaultBlock, 'DEFAULT_ANALYST_CANON block found');
if (defaultBlock){
  const block = defaultBlock[1];
  const count = (block.match(/"id"\s*:\s*"/g) || []).length;
  assert(count >= 7, `seeded canon entries >=7 (got ${count})`);
  assert(/funnel_recruiting/.test(block), 'funnel_recruiting present');
  assert(/cohort_by_month/.test(block), 'cohort_by_month present');
  assert(/abc_analysis/.test(block), 'abc_analysis present');
  assert(/"воронк"/.test(block), 'trigger "воронк" present');
  assert(/"canon":/.test(block), 'records use canon field (not body)');
  assert(/"verified": True/.test(block), 'defaults marked verified=True');
}

// 4. Two-pass логика
assert(/_derive_canon_via_model\s*\(\s*question(_full)?\s*,\s*base/.test(PY), 'two-pass called with question+base in inject');
assert(/auto_generated["']?\s*:\s*True/.test(PY), 'auto entries get auto_generated=True');
assert(/verified["']?\s*:\s*False/.test(PY), 'auto entries get verified=False');
assert(/_save_analyst_canon\(entries\)/.test(PY), 'derived canon is persisted');

// 5. Эвристика _is_report_request
const isReportFn = PY.match(/def _is_report_request[\s\S]*?return any/);
assert(isReportFn, '_is_report_request body found');
assert(/_REPORT_VERBS/.test(PY), '_REPORT_VERBS list present');
assert(/_REPORT_KEYWORDS/.test(PY), '_REPORT_KEYWORDS list present');
assert(/"построй"/.test(PY), 'verb "построй" registered');
assert(/"воронк"/.test(PY), 'keyword "воронк" registered');

// 6. Injection wiring в proxy_ai_chat_stream
const proxyBlock = PY.slice(PY.indexOf('async def proxy_ai_chat_stream'), PY.indexOf('async def proxy_ai_chat_stream') + 6000);
assert(/_inject_canon_into_messages/.test(proxyBlock),
  'proxy_ai_chat_stream calls _inject_canon_into_messages');

// 7. CRUD endpoints
assert(/@app\.get\("\/admin\/analyst-canon"\)/.test(PY), 'GET /admin/analyst-canon route registered');
assert(/@app\.post\("\/admin\/analyst-canon"\)/.test(PY), 'POST /admin/analyst-canon route registered');
assert(/@app\.post\("\/admin\/analyst-canon\/verify"\)/.test(PY), 'POST verify endpoint registered');
assert(/@app\.delete\("\/admin\/analyst-canon\/\{entry_id\}"\)/.test(PY), 'DELETE endpoint registered');
assert(/@app\.get\("\/admin\/analyst-recipes"\)/.test(PY), 'legacy GET /admin/analyst-recipes still alias');
assert(/@app\.post\("\/admin\/analyst-recipes"\)/.test(PY), 'legacy POST /admin/analyst-recipes still alias');

// 8. JS-симуляция matching (та же логика что в Python)
function matchCanon(question, entries){
  if (!question) return [];
  const q = question.toLowerCase();
  const out = [];
  for (const r of entries){
    const triggers = r.triggers || [];
    for (const t of triggers){
      if (typeof t === 'string' && t.trim() && q.includes(t.toLowerCase())){
        out.push(r); break;
      }
    }
  }
  return out;
}
function isReport(q){
  if (!q) return false;
  const ql = q.toLowerCase();
  const verbs = ['построй','построить','сделай','сделать','посчитай','покажи','сформируй','выведи','сгруппируй','проанализируй','собери','разбей'];
  const kws = ['отчет','отчёт','таблиц','сводн','воронк','когорт','abc','парето','динамик','тренд','анализ','сравн','топ','top','report'];
  return verbs.some(v => ql.includes(v)) && kws.some(k => ql.includes(k));
}
const sample = [
  {id:'funnel', triggers:['воронк','funnel'], canon:'...'},
  {id:'abc',    triggers:['abc','парето'],     canon:'...'},
];
assert(JSON.stringify(matchCanon('Построй воронку подбора', sample).map(x=>x.id)) === '["funnel"]', 'JS-match: funnel');
assert(JSON.stringify(matchCanon('Сделай ABC анализ', sample).map(x=>x.id)) === '["abc"]', 'JS-match: abc');
assert(matchCanon('Просто покажи топ-5', sample).length === 0, 'JS-match: no match on unknown');
assert(isReport('Построй воронку подбора') === true, 'isReport: true on верб+тема');
assert(isReport('Спасибо!') === false, 'isReport: false on пустое');
assert(isReport('Сделай ABC анализ по выручке') === true, 'isReport: true на ABC');

// 9. Admin UI
assert(/tab-btn-analyst-canon/.test(HTML_ADMIN), 'admin: tab button renamed → canon');
assert(/panel-analyst-canon/.test(HTML_ADMIN), 'admin: panel id renamed → canon');
assert(/Канон-библиотека/.test(HTML_ADMIN), 'admin: title says Канон-библиотека');
assert(/loadAnalystCanon/.test(HTML_ADMIN), 'admin: loadAnalystCanon function present');
assert(/saveAnalystCanon/.test(HTML_ADMIN), 'admin: saveAnalystCanon function present');
assert(/verifyAnalystCanon/.test(HTML_ADMIN), 'admin: verifyAnalystCanon function present');
assert(/deleteAnalystCanon/.test(HTML_ADMIN), 'admin: deleteAnalystCanon function present');
assert(/addAnalystCanon/.test(HTML_ADMIN), 'admin: addAnalystCanon function present');
assert(/ac-filter/.test(HTML_ADMIN), 'admin: filter dropdown present');
assert(/AUTO/.test(HTML_ADMIN), 'admin: AUTO badge in render');
assert(/непровер/.test(HTML_ADMIN), 'admin: "непроверено" badge in render');
assert(/admin\/analyst-canon\/verify/.test(HTML_ADMIN), 'admin: verify endpoint wired');
// старые имена удалены или хотя бы не визуализируются
assert(!/loadAnalystRecipes\b/.test(HTML_ADMIN), 'admin: old loadAnalystRecipes removed');

console.log(`\n=== ${pass} passed, ${fail} failed ===`);
process.exit(fail > 0 ? 1 : 0);
