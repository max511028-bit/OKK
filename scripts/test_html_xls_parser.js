// Behavioural test: parseHtmlTables + detectSpreadsheetFormat работают на реальном файле пользователя
const fs = require('fs');
const path = require('path');

const HTML = fs.readFileSync(path.join(__dirname, '..', 'analyst', 'index.html'), 'utf8');
const SAMPLE = 'C:\\Users\\user\\Downloads\\LEAD_20260514_90c5776f_6a05c985d057a.xls';

// Извлекаем нужные функции из index.html
function extractFn(src, name){
  const start = src.indexOf('function ' + name);
  if (start < 0) throw new Error('not found: ' + name);
  // ищем сбалансированную закрывающую скобку
  let depth = 0, i = start, inStr = null, esc = false;
  while (i < src.length){
    const c = src[i];
    if (inStr){
      if (esc){ esc=false; i++; continue; }
      if (c==='\\'){ esc=true; i++; continue; }
      if (c===inStr) inStr=null;
      i++; continue;
    }
    if (c==='"'||c==="'"||c==='`'){ inStr=c; i++; continue; }
    if (c==='{') depth++;
    if (c==='}'){ depth--; if (depth===0){ return src.slice(start, i+1); } }
    i++;
  }
  throw new Error('unbalanced: ' + name);
}

const code = [
  extractFn(HTML, 'detectSpreadsheetFormat'),
  extractFn(HTML, 'parseHtmlTables'),
  extractFn(HTML, 'parseSheet'),
  extractFn(HTML, 'inferTypes'),
].join('\n');

// Минимальные стабы окружения
global.TextDecoder = require('util').TextDecoder;
// DOMParser — простейший через node:
let DOMParser;
try { DOMParser = require('@xmldom/xmldom').DOMParser; }
catch(e){
  try { DOMParser = require('xmldom').DOMParser; }
  catch(e2){
    // Минимальный fallback на regex-парсер, только для теста
    DOMParser = class {
      parseFromString(s){
        return new MockDoc(s);
      }
    };
  }
}
global.DOMParser = DOMParser;

class MockDoc {
  constructor(html){ this.html = html; }
  querySelectorAll(sel){
    if (sel === 'table'){
      const tables = [];
      const re = /<table\b[^>]*>([\s\S]*?)<\/table>/gi;
      let m;
      while ((m = re.exec(this.html))) tables.push(new MockTable(m[1]));
      return tables;
    }
    return [];
  }
}
class MockTable {
  constructor(inner){ this.inner = inner; }
  querySelectorAll(sel){
    if (sel === 'tr'){
      const trs = [];
      const re = /<tr\b[^>]*>([\s\S]*?)<\/tr>/gi;
      let m;
      while ((m = re.exec(this.inner))) trs.push(new MockTr(m[1]));
      return trs;
    }
    return [];
  }
}
class MockTr {
  constructor(inner){ this.inner = inner; }
  querySelectorAll(sel){
    if (sel === 'th,td'){
      const tds = [];
      const re = /<(?:th|td)\b[^>]*>([\s\S]*?)<\/(?:th|td)>/gi;
      let m;
      while ((m = re.exec(this.inner))){
        // strip tags inside
        const text = m[1].replace(/<[^>]*>/g, '').replace(/&nbsp;/g,' ').replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"');
        tds.push({ textContent: text });
      }
      return tds;
    }
    return [];
  }
}
global.DOMParser = class { parseFromString(s){ return new MockDoc(s); } };

eval(code);

let pass = 0, fail = 0;
function ok(n){ console.log('\x1b[32m✓\x1b[0m', n); pass++; }
function bad(n, r){ console.log('\x1b[31m✗\x1b[0m', n, '\n   ', r); fail++; }

if (!fs.existsSync(SAMPLE)){
  console.log('SKIP: sample file not available at', SAMPLE);
  process.exit(0);
}

const buf = fs.readFileSync(SAMPLE);
// Конвертируем Buffer в ArrayBuffer как в браузере
const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);

const fmt = detectSpreadsheetFormat(ab);
fmt === 'html' ? ok('detectSpreadsheetFormat: пользовательский .xls определён как html') : bad('detect', 'got ' + fmt);

const decoded = new TextDecoder('utf-8').decode(ab);
let sheets;
try { sheets = parseHtmlTables(decoded, 'LEAD_20260514.xls'); }
catch(e){ bad('parseHtmlTables doesn\'t throw', e.message); process.exit(1); }

sheets.length > 0 ? ok(`parseHtmlTables: извлечено ${sheets.length} лист(ов)`) : bad('parseHtmlTables non-empty', '0 sheets');

const s = sheets[0];
s.columns.length > 0 ? ok(`лист: колонок=${s.columns.length}`) : bad('columns', '0');
s.rows.length > 0 ? ok(`лист: строк=${s.rows.length}`) : bad('rows', '0');

// Проверка что строки реально содержат данные
const firstRow = s.rows[0];
const hasAnyData = Object.values(firstRow).some(v => v != null && v !== '');
hasAnyData ? ok('первая строка содержит данные') : bad('row data', 'empty row');

console.log(`\nКолонки: ${s.columns.slice(0, 5).join(' | ')}${s.columns.length>5?' ...':''}`);
console.log(`Пример строки: ${JSON.stringify(Object.fromEntries(Object.entries(firstRow).slice(0, 3)))}`);

console.log(`\n=== ${pass} passed, ${fail} failed ===`);
process.exit(fail > 0 ? 1 : 0);
