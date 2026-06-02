// Smoke + behavioural tests for analyst Sprint 2 group 1.
// Run: node scripts/test_analyst_sprint2.js
const fs = require('fs');
const path = require('path');

const HTML = fs.readFileSync(path.join(__dirname, '..', 'analyst', 'index.html'), 'utf8');
const PY   = fs.readFileSync(path.join(__dirname, '..', 'tasks', 'api', 'main.py'), 'utf8');

let pass = 0, fail = 0;
function ok(name){ console.log(`\x1b[32m✓\x1b[0m ${name}`); pass++; }
function bad(name, reason){ console.log(`\x1b[31m✗\x1b[0m ${name}\n   ${reason}`); fail++; }
function assert(cond, name, reason){ cond ? ok(name) : bad(name, reason || 'assertion failed'); }

// ---- 1. Frontend: renderAssistantFinal triple-render bug ----
// The clarif block must be rendered AT MOST once: only one place creates `cl.className = 'clarif'`
const clarifMatches = HTML.match(/cl\.className\s*=\s*'clarif'/g) || [];
assert(clarifMatches.length === 1,
  'renderAssistantFinal: clarif block constructed exactly once',
  `expected 1 occurrence, got ${clarifMatches.length}`);

// The function must early-return in clarification mode (return; statement after clarif append)
const renderFn = HTML.slice(HTML.indexOf('function renderAssistantFinal'), HTML.indexOf('function renderAssistantFinal') + 4000);
assert(/return;\s*\/\/\s*НЕ рендерим прозу/.test(renderFn) || /return;[^}]*\}\s*\n\s*\/\/ === ACTION MODE/.test(renderFn),
  'renderAssistantFinal: early-return between clarification and action mode',
  'no early return guard found');

// allowedKeys allowlist must exist and exclude "Ответ"
assert(/const allowedKeys = new Set\(\[[^\]]*'Что понял'[^\]]*\]\)/.test(renderFn),
  'renderAssistantFinal: allowedKeys allowlist present',
  'allowedKeys allowlist missing');
assert(!/allowedKeys[^\}]*'Ответ'/.test(renderFn),
  'renderAssistantFinal: "Ответ" prose section blocked',
  '"Ответ" must NOT be in allowedKeys');

// suggested_actions chips: must read plan.suggested_actions and create buttons
assert(/plan\.suggested_actions/.test(renderFn),
  'renderAssistantFinal: reads plan.suggested_actions');
assert(/askAI\(txt\)/.test(renderFn),
  'renderAssistantFinal: chip click triggers askAI()');

// ---- 2. Frontend: extractJson returns suggested_actions ----
// extractJson detects via `r.steps || r.clarification` — verify the whole object is returned,
// not just the clarification string. Simulate by evaluating.
const extractFn = HTML.slice(HTML.indexOf('function extractJson'), HTML.indexOf('function normalizePlan'));
const tryParseFn = 'function tryParse(s){ if (!s) return null; let t = s.trim().replace(/,(\\s*[}\\]])/g, "$1"); try { return JSON.parse(t); } catch(_){} return null; }';
const collectFn = HTML.match(/function collectTopLevelObjects[\s\S]*?\n\}/)[0];
const sandbox = `${extractFn}\n${tryParseFn}\n${collectFn}\nmodule.exports = extractJson;`;
fs.writeFileSync(path.join(__dirname, '_extract_temp.js'), sandbox);
const extractJson = require('./_extract_temp.js');
fs.unlinkSync(path.join(__dirname, '_extract_temp.js'));

const sampleClarif = '```json\n{"clarification":"Что считать эффективностью?","suggested_actions":["Покажи топ-10 по марже","Сравни города по выручке","Найди сделки старше 30 дней"]}\n```';
const r1 = extractJson(sampleClarif);
assert(r1 && r1.clarification && Array.isArray(r1.suggested_actions) && r1.suggested_actions.length === 3,
  'extractJson: parses {clarification, suggested_actions:[3]}',
  `got: ${JSON.stringify(r1)}`);

const sampleAction = 'Что понял: топ-5.\nПлан: groupby + sort + top.\n```json\n{"steps":[{"op":"groupby","by":["Менеджер"],"agg":{"Выручка":"sum"}}],"summary":"топ"}\n```';
const r2 = extractJson(sampleAction);
assert(r2 && Array.isArray(r2.steps) && r2.steps.length === 1 && !r2.clarification,
  'extractJson: parses action mode {steps, summary}',
  `got: ${JSON.stringify(r2)}`);

// Last-fenced rule: when prompt contains few-shot example fences before final answer,
// extractJson must pick the LAST one (the actual answer).
const withFewshot = '```json\n{"steps":[{"op":"trim","columns":["A"]}],"summary":"example"}\n```\n\nFinal:\n```json\n{"clarification":"уточни","suggested_actions":["a","b","c"]}\n```';
const r3 = extractJson(withFewshot);
assert(r3 && r3.clarification === 'уточни',
  'extractJson: picks LAST fenced block (skips few-shot examples)',
  `got: ${JSON.stringify(r3)}`);

// ---- 3. Frontend: isClarification sentinel detection ----
const isClarifFn = HTML.match(/function isClarification[\s\S]*?\n\}/)[0];
eval(isClarifFn);
assert(isClarification('{"clarification":"x"}') === true,
  'isClarification: detects "clarification" sentinel');
assert(isClarification('Топ-5 менеджеров') === false,
  'isClarification: false on plain action text');

// ---- 4. Backend prompt: anti-prose rules present ----
const promptStart = PY.indexOf('"analyst": (');
const promptEnd = PY.indexOf('"chat": (', promptStart);
const analystPrompt = PY.slice(promptStart, promptEnd);

assert(/ТЫ НЕ ПИШЕШЬ ЭССЕ/.test(analystPrompt),
  'prompt: anti-prose directive present');
assert(/suggested_actions/.test(analystPrompt),
  'prompt: suggested_actions schema documented');
assert(/РОВНО 3/.test(analystPrompt),
  'prompt: requires exactly 3 suggested actions');
assert(/РЕЖИМ A.*ДЕЙСТВИЕ/s.test(analystPrompt) && /РЕЖИМ B.*УТОЧНЕНИЕ/s.test(analystPrompt),
  'prompt: two-mode contract (A/B) documented');
assert(/ЗАПРЕЩЕНО/.test(analystPrompt) && /давайте/.test(analystPrompt) && /рассмотрим/.test(analystPrompt),
  'prompt: filler words explicitly forbidden');
assert(/Топ-5 менеджеров/.test(analystPrompt),
  'prompt: few-shot action example present');
assert(/Оцени эффективность/.test(analystPrompt),
  'prompt: few-shot clarification example present');
assert(/USER_DATA_BEGIN/.test(analystPrompt),
  'prompt: Sprint 1 data-marker injection still present');

// ---- 5. Sanity: JS still parseable as a whole (basic brace balance) ----
const scripts = [...HTML.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)]
  .filter(m => !/src=/.test(m[0]))
  .map(m => m[1])
  .join('\n');
// String/regex/comment-aware brace counter
let braces = 0, parens = 0;
let i = 0, inStr = null, inLine = false, inBlock = false, inRe = false, prev = '';
while (i < scripts.length){
  const c = scripts[i], n = scripts[i+1];
  if (inLine){ if (c === '\n') inLine = false; i++; continue; }
  if (inBlock){ if (c === '*' && n === '/'){ inBlock = false; i += 2; continue; } i++; continue; }
  if (inStr){
    if (c === '\\'){ i += 2; continue; }
    if (c === inStr) inStr = null;
    i++; continue;
  }
  if (inRe){
    if (c === '\\'){ i += 2; continue; }
    if (c === '/'){ inRe = false; i++; continue; }
    if (c === '[') { while (i < scripts.length && scripts[i] !== ']'){ if (scripts[i]==='\\') i++; i++; } }
    i++; continue;
  }
  if (c === '/' && n === '/'){ inLine = true; i += 2; continue; }
  if (c === '/' && n === '*'){ inBlock = true; i += 2; continue; }
  if (c === '"' || c === "'" || c === '`'){ inStr = c; i++; continue; }
  // crude regex detection: / after operator/keyword/open
  if (c === '/' && /[=(,;:!&|?{}\n[]/.test(prev || '\n')){ inRe = true; i++; continue; }
  if (c === '{') braces++; else if (c === '}') braces--;
  if (c === '(') parens++; else if (c === ')') parens--;
  if (!/\s/.test(c)) prev = c;
  i++;
}
assert(braces === 0, 'JS brace balance', `unbalanced: ${braces}`);
assert(parens === 0, 'JS paren balance', `unbalanced: ${parens}`);

// ---- summary ----
console.log(`\n=== ${pass} passed, ${fail} failed ===`);
process.exit(fail > 0 ? 1 : 0);
