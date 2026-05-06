#!/usr/bin/env python3
"""Injects floating AI assistant panel (Ollama-powered) into HTML dashboards."""
import sys

CONFIGS = {
    'wb': {
        'title': 'WB Аналитик',
        'greeting': 'Привет! Я анализирую данные WB аутсорсинга — выработку, штрафы и операции сотрудников.<br><br>Спросите о топ-сотрудниках, зоне риска, причинах штрафов или выработке по складам.',
        'suggestions': [
            'Кто в зоне риска по выработке?',
            'Топ-5 по заработку за период',
            'Самые частые причины штрафов',
            'Средняя выработка по складам',
            'Сколько сотрудников работало?',
            'На каком складе больше всего штрафов?',
        ],
        'system_prompt': r"""Ты — AI-аналитик дашборда WB Аутсорсинг. Анализируешь данные по выработке, штрафам и операциям складских сотрудников.

Правила:
- Отвечай ТОЛЬКО на русском языке
- Используй ТОЛЬКО цифры из предоставленных данных
- Если данных нет — прямо скажи
- Без вступлений типа "Конечно!", "Отличный вопрос!"

Формат: Факты (цифры) → Вывод → Рекомендация. Максимум 8 предложений.
Используй **жирный** для имён и ключевых цифр.

АКТУАЛЬНЫЕ ДАННЫЕ:
""",
        'build_ctx': r"""function _aiBuildCtx(){
  var lines=['=== ДАННЫЕ WB АУТСОРСИНГ ==='];
  var v=[],f=[],o=[];
  if(typeof periods!=='undefined'){
    if(typeof curPeriod!=='undefined'&&curPeriod!=='all'&&periods[curPeriod]){
      v=periods[curPeriod].vyrab||[];f=periods[curPeriod].fines||[];o=periods[curPeriod].ops||[];
    } else {
      Object.values(periods).forEach(function(p){v=v.concat(p.vyrab||[]);f=f.concat(p.fines||[]);o=o.concat(p.ops||[]);});
    }
  }
  var prd=(typeof curPeriod!=='undefined'?curPeriod:'неизвестен');
  lines.push('Период: '+prd);
  lines.push('Сотрудников: '+v.length);
  if(v.length){
    var pn2=function(x){return parseFloat(String(x).replace(',','.'))||0;};
    var tot=v.reduce(function(s,r){return s+pn2(r.netEarn);},0);
    lines.push('Общий ФОТ (чистая выработка): '+tot.toFixed(0)+' руб');
    var avg=tot/v.length;
    lines.push('Средняя выработка: '+avg.toFixed(0)+' руб');
    var sorted=v.slice().sort(function(a,b){return pn2(b.netEarn)-pn2(a.netEarn);});
    lines.push('Топ-5: '+sorted.slice(0,5).map(function(r){return r.fio+' ('+pn2(r.netEarn).toFixed(0)+'р)';}).join(', '));
    var risk=v.filter(function(r){return pn2(r.netEarn)<=0||pn2(r.days)<5;});
    lines.push('В зоне риска (≤0р или <5 дней): '+risk.length+' чел');
    if(risk.length)lines.push('Риск-список: '+risk.slice(0,5).map(function(r){return r.fio;}).join(', '));
    var whs={};
    v.forEach(function(r){whs[r.wh]=(whs[r.wh]||0)+1;});
    lines.push('По складам: '+Object.entries(whs).sort(function(a,b){return b[1]-a[1];}).slice(0,5).map(function(e){return e[0]+':'+e[1];}).join(', '));
  }
  if(f.length){
    var pn2=function(x){return parseFloat(String(x).replace(',','.'))||0;};
    var totF=f.reduce(function(s,r){return s+pn2(r.amount);},0);
    lines.push('Штрафов: '+f.length+', сумма: '+totF.toFixed(0)+' руб');
    var reasons={};
    f.forEach(function(r){if(r.reason)reasons[r.reason]=(reasons[r.reason]||0)+1;});
    var topR=Object.entries(reasons).sort(function(a,b){return b[1]-a[1];}).slice(0,3).map(function(e){return e[0]+'('+e[1]+')';}).join(', ');
    lines.push('Топ причин штрафов: '+topR);
  }
  if(o.length){
    var opT={};
    o.forEach(function(r){if(r.opType)opT[r.opType]=(opT[r.opType]||0)+1;});
    lines.push('Операций: '+o.length+', типы: '+Object.entries(opT).sort(function(a,b){return b[1]-a[1];}).slice(0,4).map(function(e){return e[0]+':'+e[1];}).join(', '));
  }
  return lines.join('\\n');
}"""
    },

    'finance': {
        'title': 'Финансовый аналитик',
        'greeting': 'Привет! Я анализирую финансовые показатели компании — рентабельность, тренды и аномалии.<br><br>Спросите о прибыльных проектах, трендах по городам или аномалиях в данных.',
        'suggestions': [
            'Какой проект самый рентабельный?',
            'Где наблюдаются аномалии?',
            'Сравни показатели по городам',
            'Какой тренд по выручке?',
            'Топ клиентов по прибыли',
            'Сравни 2024 и 2025 год',
        ],
        'system_prompt': r"""Ты — AI-аналитик финансового дашборда STH Group. Анализируешь рентабельность, P&L, тренды по проектам, городам и клиентам.

Правила:
- Отвечай ТОЛЬКО на русском языке
- Используй данные из контекста. Если данных нет — скажи об этом
- Без вступлений типа "Конечно!", "Отличный вопрос!"
- Формат: Факты → Вывод → Рекомендация. Максимум 8 предложений.
- Используй **жирный** для названий проектов и ключевых цифр

КОНТЕКСТ:
""",
        'build_ctx': r"""function _aiBuildCtx(){
  var lines=['=== ФИНАНСОВЫЙ ДАШБОРД STH GROUP ==='];
  // Try to get data from global chart data if available
  if(typeof window.chartData!=='undefined'){
    try{lines.push(JSON.stringify(window.chartData).slice(0,2000));}catch(e){}
  }
  if(typeof window.dashboardData!=='undefined'){
    try{lines.push(JSON.stringify(window.dashboardData).slice(0,2000));}catch(e){}
  }
  lines.push('(Данные встроены в дашборд — задай конкретный вопрос по видимым графикам)');
  return lines.join('\\n');
}"""
    },

    'kp': {
        'title': 'КП Ассистент',
        'greeting': 'Привет! Помогу с расчётом коммерческих предложений и анализом рынка зарплат.<br><br>Спросите о стоимости найма, рыночных ставках или как рассчитывается итоговая цена.',
        'suggestions': [
            'Как рассчитывается итоговая цена КП?',
            'Что влияет на стоимость найма?',
            'Какова средняя рыночная ЗП?',
            'Как снизить стоимость подбора?',
            'Чем отличаются типы смен?',
            'Как учитываются штрафы в расчёте?',
        ],
        'system_prompt': r"""Ты — ассистент по расчёту коммерческих предложений (КП) для аутсорсинга складского персонала STH.

Правила:
- Отвечай ТОЛЬКО на русском языке
- Объясняй формулы и расчёты простым языком
- Давай практические советы по оптимизации стоимости
- Без вступлений типа "Конечно!", "Отличный вопрос!"
- Формат: чёткий, структурированный. Максимум 8 предложений.

КОНТЕКСТ КП КАЛЬКУЛЯТОРА:
""",
        'build_ctx': r"""function _aiBuildCtx(){
  var lines=['=== КП КАЛЬКУЛЯТОР ==='];
  var fields=['projectName','city','hcCount','duration','shift','salary'];
  fields.forEach(function(id){
    var el=document.getElementById(id);
    if(el&&el.value)lines.push(id+': '+el.value);
  });
  // Try to get result if calculated
  var result=document.querySelector('.result-value,.total-price,.kp-total');
  if(result)lines.push('Рассчитанная стоимость: '+result.textContent.trim());
  var salary=document.getElementById('hh_salary_result');
  if(salary)lines.push('Рыночная ЗП (hh.ru): '+salary.textContent.trim());
  return lines.join('\\n');
}"""
    },

    'hr-game': {
        'title': 'HR Ассистент',
        'greeting': 'Привет! Помогу с вопросами по HR-процессам найма и онбординга складского персонала.<br><br>Задайте вопрос о процессах подбора, оформления или адаптации сотрудников.',
        'suggestions': [
            'Как правильно провести собеседование?',
            'Какие документы нужны при оформлении?',
            'Как снизить текучку персонала?',
            'Критерии отбора кандидатов на склад',
            'Как провести онбординг новичка?',
            'Типичные ошибки рекрутеров',
        ],
        'system_prompt': r"""Ты — HR-ассистент компании STH по найму складского персонала. Отвечаешь на вопросы по процессам подбора, оформления и адаптации сотрудников.

Правила:
- Отвечай ТОЛЬКО на русском языке
- Давай конкретные практические советы
- Без вступлений типа "Конечно!", "Отличный вопрос!"
- Формат: структурированный, с примерами. Максимум 8 предложений.

КОНТЕКСТ:
""",
        'build_ctx': r"""function _aiBuildCtx(){
  return '=== HR-ИГРА STH ===\nИнструмент для обучения рекрутеров по найму складского персонала.';
}"""
    },
}

CSS = '''
/* ===== AI FLOATING PANEL ===== */
#_ai_btn{position:fixed;bottom:24px;right:24px;width:54px;height:54px;border-radius:50%;background:linear-gradient(135deg,#3b82f6,#8b5cf6);border:none;color:#fff;font-size:24px;cursor:pointer;z-index:9998;box-shadow:0 4px 20px rgba(59,130,246,.5);transition:transform .2s,box-shadow .2s;display:flex;align-items:center;justify-content:center;}
#_ai_btn:hover{transform:scale(1.1);box-shadow:0 6px 28px rgba(59,130,246,.7);}
#_ai_panel{position:fixed;bottom:90px;right:24px;width:400px;height:580px;background:#161b22;border:1px solid rgba(255,255,255,.1);border-radius:18px;box-shadow:0 24px 80px rgba(0,0,0,.6);z-index:9997;display:none;flex-direction:column;overflow:hidden;font-family:Inter,system-ui,sans-serif;}
#_ai_panel_head{padding:14px 16px;background:#0d1117;border-bottom:1px solid rgba(255,255,255,.08);display:flex;align-items:center;gap:10px;flex-shrink:0;}
#_ai_panel_head .ai-title{font-weight:700;color:#f0f6fc;font-size:14px;flex:1;}
#_ai_model_sel{background:#161b22;border:1px solid rgba(255,255,255,.12);border-radius:8px;color:#94a3b8;font-size:11px;padding:4px 8px;max-width:160px;}
#_ai_clear{background:none;border:none;color:#64748b;cursor:pointer;font-size:18px;padding:0 4px;}
#_ai_clear:hover{color:#94a3b8;}
#_ai_msgs{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px;scroll-behavior:smooth;}
#_ai_msgs::-webkit-scrollbar{width:4px;}
#_ai_msgs::-webkit-scrollbar-track{background:transparent;}
#_ai_msgs::-webkit-scrollbar-thumb{background:rgba(255,255,255,.1);border-radius:4px;}
._ai_msg{max-width:85%;padding:10px 14px;border-radius:12px;font-size:13px;line-height:1.6;word-break:break-word;}
._ai_msg.user{align-self:flex-end;background:rgba(59,130,246,.2);border:1px solid rgba(59,130,246,.3);color:#e2e8f0;border-bottom-right-radius:3px;}
._ai_msg.bot{align-self:flex-start;background:#1e293b;border:1px solid rgba(255,255,255,.07);color:#cbd5e1;border-bottom-left-radius:3px;}
._ai_msg.bot strong{color:#f0f6fc;}
._ai_msg table{border-collapse:collapse;width:100%;margin:6px 0;font-size:12px;}
._ai_msg th,._ai_msg td{border:1px solid rgba(255,255,255,.1);padding:4px 8px;text-align:left;}
._ai_msg th{background:rgba(255,255,255,.05);}
#_ai_chips{display:flex;flex-wrap:wrap;gap:6px;padding:0 14px 10px;flex-shrink:0;}
._ai_chip{background:rgba(59,130,246,.1);border:1px solid rgba(59,130,246,.25);border-radius:20px;padding:5px 12px;font-size:11px;color:#60a5fa;cursor:pointer;white-space:nowrap;transition:background .15s;}
._ai_chip:hover{background:rgba(59,130,246,.2);}
#_ai_inp_row{padding:10px 14px;border-top:1px solid rgba(255,255,255,.08);display:flex;gap:8px;flex-shrink:0;}
#_ai_inp{flex:1;background:#0d1117;border:1px solid rgba(255,255,255,.12);border-radius:10px;color:#f0f6fc;font-size:13px;padding:9px 12px;outline:none;resize:none;font-family:inherit;}
#_ai_inp:focus{border-color:rgba(59,130,246,.5);}
#_ai_send{background:#3b82f6;border:none;border-radius:10px;color:#fff;padding:9px 14px;cursor:pointer;font-size:18px;transition:background .15s;flex-shrink:0;}
#_ai_send:hover{background:#2563eb;}
#_ai_send:disabled{background:#334155;cursor:not-allowed;}
'''

def make_js(cfg, dashboard_type):
    suggs_js = '[' + ','.join(f'"{s}"' for s in cfg['suggestions']) + ']'
    system_prompt = cfg['system_prompt'].replace('`', '\\`').replace('${', '\\${')
    greeting_escaped = cfg['greeting'].replace("'", "\\'")
    title = cfg['title']

    return f"""
// ===== AI FLOATING PANEL ({dashboard_type.upper()}) =====
(function(){{
const _OLLAMA = 'http://178.63.16.109:11434/api';
const _SUGGS = {suggs_js};
let _hist=[], _models=[], _open=false;

{cfg['build_ctx']}

function _fmtAI(t){{
  t=t.replace(/[\\u4e00-\\u9fff\\u3040-\\u30ff]{{1,3}}(?=\\s|[\\u0400-\\u04ff])/g,'');
  t=t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  const tr=/(\\.+\\|\\n)((?:\\|[-:])+\\|\\n)((?:\\|.+\\|\\n?)+)/g;
  t=t.replace(tr,function(_,h,s,b){{
    const hc=h.split('|').filter(c=>c.trim()).map(c=>`<th>${{c.trim()}}</th>`).join('');
    const rows=b.trim().split('\\n').map(r=>{{const cells=r.split('|').filter(c=>c.trim()).map(c=>`<td>${{c.trim()}}</td>`).join('');return`<tr>${{cells}}</tr>`;}}).join('');
    return`<table><thead><tr>${{hc}}</tr></thead><tbody>${{rows}}</tbody></table>`;
  }});
  return t.replace(/\\*\\*(.+?)\\*\\*/g,'<strong>$1</strong>')
    .replace(/\\*(.+?)\\*/g,'<em>$1</em>')
    .replace(/^- (.+)$/gm,'• $1')
    .replace(/\\n/g,'<br>');
}}

function _addMsg(role,html){{
  const box=document.getElementById('_ai_msgs');
  if(!box)return null;
  const d=document.createElement('div');
  d.className='_ai_msg '+role;
  d.innerHTML=html;
  box.appendChild(d);
  box.scrollTop=box.scrollHeight;
  return d;
}}

function _loadModels(){{
  const sel=document.getElementById('_ai_model_sel');
  if(!sel)return;
  sel.innerHTML='<option>⏳ Загрузка...</option>';
  fetch(`${{_OLLAMA}}/tags`).then(function(r){{if(!r.ok)throw new Error('HTTP '+r.status);return r.json();}}).then(function(data){{
    _models=(data.models||[]).map(m=>m.name).sort();
    if(!_models.length)throw new Error('Нет моделей на сервере');
    sel.innerHTML=_models.map(m=>`<option value="${{m}}">${{m}}</option>`).join('');
    const pref=['qwen3-coder-next:cloud','llama3.2:3b','qwen3:30b'];
    const def=pref.find(p=>_models.includes(p))||_models[0];
    sel.value=def;
  }}).catch(function(e){{
    sel.innerHTML='<option value="">⚠️ '+(e.message||'Ошибка')+' — клик для повтора</option>';
    sel.onclick=function(){{if(!_models.length){{sel.onclick=null;_loadModels();}}}};
  }});
}}

async function _ask(msg){{
  const btn=document.getElementById('_ai_send');
  const inp=document.getElementById('_ai_inp');
  if(btn)btn.disabled=true;
  if(inp)inp.disabled=true;
  _addMsg('user',_fmtAI(msg));
  const thinking=_addMsg('bot','<span>⏳ Анализирую...</span>');
  try{{
    const model=document.getElementById('_ai_model_sel')?.value||'';
    if(!model)throw new Error('Выберите модель');
    const sysContent=`{system_prompt}`+_aiBuildCtx();
    const messages=[{{role:'system',content:sysContent}},..._hist.map(h=>h)];
    messages.push({{role:'user',content:msg}});
    const payload={{model,messages,stream:false,think:false}};
    const res=await fetch(`${{_OLLAMA}}/chat`,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}});
    if(!res.ok)throw new Error('HTTP '+res.status);
    const data=await res.json();
    const reply=data.message?.content||'';
    if(thinking)thinking.innerHTML=_fmtAI(reply);
    _hist.push({{role:'user',content:msg}},{{role:'assistant',content:reply}});
    if(_hist.length>20)_hist.splice(0,2);
  }}catch(e){{
    if(thinking){{const sp=document.createElement('span');sp.style.color='#f85149';sp.textContent='⚠️ '+(e.message||'Ошибка');thinking.innerHTML='';thinking.appendChild(sp);}}
  }}
  if(btn)btn.disabled=false;
  if(inp){{inp.disabled=false;inp.focus();}}
  const box=document.getElementById('_ai_msgs');
  if(box)box.scrollTop=box.scrollHeight;
}}

function _send(){{
  const inp=document.getElementById('_ai_inp');
  if(!inp)return;
  const msg=inp.value.trim();
  if(!msg)return;
  inp.value='';
  _ask(msg);
}}

function _chip(txt){{
  const inp=document.getElementById('_ai_inp');
  if(inp)inp.value=txt;
  _send();
}}

function _toggle(){{
  _open=!_open;
  const panel=document.getElementById('_ai_panel');
  if(panel)panel.style.display=_open?'flex':'none';
  if(_open&&!_models.length)setTimeout(_loadModels,50);
}}

function _clear(){{
  _hist=[];
  const box=document.getElementById('_ai_msgs');
  if(box)box.innerHTML='<div class="_ai_msg bot">{greeting_escaped}</div>';
  const chips=document.getElementById('_ai_chips');
  if(chips)chips.style.display='flex';
}}

document.addEventListener('DOMContentLoaded',function(){{
  // CSS
  const st=document.createElement('style');
  st.textContent=`{CSS.replace(chr(96), chr(96))}`;
  document.head.appendChild(st);

  // Button
  const btn=document.createElement('button');
  btn.id='_ai_btn';
  btn.innerHTML='🤖';
  btn.title='{title}';
  btn.onclick=_toggle;
  document.body.appendChild(btn);

  // Panel
  const panel=document.createElement('div');
  panel.id='_ai_panel';
  const suggs_html=_SUGGS.map(s=>`<span class="_ai_chip" onclick="_chip('${{s}}')">${{s}}</span>`).join('');
  panel.innerHTML=`
    <div id="_ai_panel_head">
      <span style="font-size:20px">🤖</span>
      <span class="ai-title">{title}</span>
      <select id="_ai_model_sel"></select>
      <button id="_ai_clear" onclick="_clear()" title="Очистить чат">🗑</button>
      <button id="_ai_close" onclick="_toggle()" title="Закрыть" style="background:none;border:none;color:#64748b;cursor:pointer;font-size:20px;padding:0 4px;">✕</button>
    </div>
    <div id="_ai_msgs">
      <div class="_ai_msg bot">{greeting_escaped}</div>
    </div>
    <div id="_ai_chips">${{suggs_html}}</div>
    <div id="_ai_inp_row">
      <textarea id="_ai_inp" rows="1" placeholder="Задайте вопрос..."></textarea>
      <button id="_ai_send" onclick="_send()">➤</button>
    </div>`;
  document.body.appendChild(panel);

  // Enter to send
  setTimeout(function(){{
    const inp=document.getElementById('_ai_inp');
    if(inp)inp.addEventListener('keydown',function(e){{if(e.key==='Enter'&&!e.shiftKey){{e.preventDefault();_send();}}}});
  }},100);

  // Hide chips after first use
  document.addEventListener('click',function(e){{
    if(e.target.classList.contains('_ai_chip')){{
      const chips=document.getElementById('_ai_chips');
      if(chips)chips.style.display='none';
    }}
  }});
}});

window._chip=_chip;
window._toggle=_toggle;
window._clear=_clear;
}})();
"""

def chr(n):
    return __builtins__['chr'](n) if isinstance(__builtins__, dict) else __builtins__.chr(n)


def inject(filename, dashboard_type):
    if dashboard_type not in CONFIGS:
        print(f'Unknown dashboard type: {dashboard_type}. Use: {list(CONFIGS.keys())}')
        return

    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()

    # Remove existing AI panel if any
    import re
    content = re.sub(r'\n// ===== AI FLOATING PANEL.*?window\._clear=_clear;\s*\}\)\(\);\n', '', content, flags=re.DOTALL)

    if '</body>' not in content:
        print(f'WARNING: no </body> in {filename}')
        return

    js = make_js(CONFIGS[dashboard_type], dashboard_type)
    injection = f'\n<script>\n{js}\n</script>\n'
    content = content.replace('</body>', injection + '</body>', 1)

    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'AI panel ({dashboard_type}) injected into {filename}')


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(f'Usage: python inject_ai.py <file.html> <type>')
        print(f'Types: {list(CONFIGS.keys())}')
        sys.exit(1)
    inject(sys.argv[1], sys.argv[2])
