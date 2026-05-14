#!/usr/bin/env python3
"""Injects floating AI assistant panel (Ollama-powered) into HTML dashboards."""
import sys

CONFIGS = {
    'wb': {
        'title': 'WB Аналитик',
        'greeting': 'Привет! Я анализирую данные WB аутсорсинга — выработку, штрафы и операции сотрудников.<br><br>Спросите о топ-сотрудниках, зоне риска, причинах штрафов или выработке по складам.',
        'suggestions': [
            'Кто сегодня в риске — штрафы >20% от ЗП?',
            'Топ-10 по ₽/час за неделю — кого премировать?',
            'У кого скачок штрафов за последние 7 дней?',
            'Кто в рейтинге F — оставлять или увольнять?',
            'Сравни сдельных и почасовых: где выгоднее?',
            'На каких операциях падает производительность?',
            'Какие склады лидеры/аутсайдеры по ФОТ?',
            'Кто новый, но уже в зоне A/B — потенциал бригадира?',
            'Покажи дни, когда выработка ниже плана.',
            'Прогноз ФОТ на конец месяца.',
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
            'Топ-3 убыточных проекта в этом периоде.',
            'Где маржа упала больше всего месяц-к-месяцу?',
            'Какие РРП-направления просели?',
            'По каким клиентам пора повышать цену?',
            'Что общего у проектов с рентабельностью >20%?',
            'Где расходы растут быстрее выручки?',
            'Сравни Москву и Краснодар по марже.',
            'Прогноз кассового разрыва на следующий месяц.',
            'Какие проекты съедают всю административку?',
            'Какие клиенты под угрозой ухода?',
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
  try{
    if(typeof D==='undefined'){lines.push('Данные ещё не загружены.');return lines.join('\\n');}
    // Активная вкладка
    var act=document.querySelector('.tab-content.active');
    var tabName=act?act.id.replace('tab-',''):'general';
    lines.push('Активная вкладка: '+tabName);
    // Период
    var typeSel=document.getElementById(tabName+'PeriodType')||document.getElementById('generalPeriodType');
    var valSel=document.getElementById(tabName+'PeriodVal')||document.getElementById('generalPeriodVal');
    var typeV=typeSel?typeSel.value:'all',valV=valSel?valSel.value:'';
    var keys=(typeof getMonthKeys==='function')?getMonthKeys(typeV,valV):[];
    var label=(typeof getPeriodLabel==='function')?getPeriodLabel(typeV,valV):valV;
    lines.push('Период: '+label+' ('+keys.length+' мес.)');
    // Общая статистика
    lines.push('Всего месяцев данных: '+D.monthly.length+', проектов: '+new Set(Object.values(D.projects_per_month).flat().map(function(p){return p.name;})).size+', клиентов: '+(D.clients||[]).length+', городов: '+(D.cities||[]).length);
    // P&L агрегат по периоду
    if(typeof aggregatePlRows==='function'){
      var rows=aggregatePlRows(keys);
      var pl={};
      rows.forEach(function(r){pl[r.label]=r.value;});
      var rev=pl['Выручка']||pl['Выручка с НДС']||0;
      var prof=pl['Прибыль']||pl['Чистая прибыль']||pl['Маржинальная прибыль']||0;
      var marg=rev>0?(prof/rev*100).toFixed(1):'0';
      lines.push('Выручка: '+Math.round(rev).toLocaleString('ru-RU')+' руб');
      lines.push('Прибыль: '+Math.round(prof).toLocaleString('ru-RU')+' руб');
      lines.push('Рентабельность: '+marg+'%');
    }
    // Топ-проекты по выручке за период
    var acc={};
    keys.forEach(function(k){(D.projects_per_month[k]||[]).forEach(function(p){
      if(!acc[p.name])acc[p.name]={rev:0,fin:0};
      acc[p.name].rev+=p.rev||0;acc[p.name].fin+=(p.rev||0)*((p.margin||0)/100);
    });});
    var projs=Object.entries(acc).map(function(e){return{n:e[0],rev:e[1].rev,fin:e[1].fin,mar:e[1].rev>0?e[1].fin/e[1].rev*100:0};}).sort(function(a,b){return b.rev-a.rev;});
    if(projs.length){
      lines.push('Топ-5 проектов по выручке:');
      projs.slice(0,5).forEach(function(p,i){lines.push((i+1)+'. '+p.n+': '+Math.round(p.rev).toLocaleString('ru-RU')+'р выр., '+p.mar.toFixed(1)+'% маржа');});
      var loss=projs.filter(function(p){return p.fin<0;});
      if(loss.length){lines.push('Убыточных проектов: '+loss.length+' — '+loss.slice(0,5).map(function(p){return p.n+'('+p.mar.toFixed(0)+'%)';}).join(', '));}
      var byMar=projs.filter(function(p){return p.rev>50000;}).slice().sort(function(a,b){return b.mar-a.mar;});
      if(byMar.length){lines.push('Топ-3 по марже (среди значимых): '+byMar.slice(0,3).map(function(p){return p.n+'('+p.mar.toFixed(1)+'%)';}).join(', '));}
    }
    // Дивизионы
    if(typeof aggregateDivisions==='function'){
      var divs=aggregateDivisions(keys);
      if(divs&&divs.length){
        lines.push('По дивизионам (выручка/маржа):');
        divs.slice(0,5).forEach(function(d){lines.push('— '+d.div+': '+Math.round(d.revSum).toLocaleString('ru-RU')+'р, '+d.margin.toFixed(1)+'%');});
      }
    }
    // Динамика по последним месяцам
    if(D.monthly&&D.monthly.length){
      var last3=D.monthly.slice(-3);
      lines.push('Последние месяцы: '+last3.map(function(m){return m.label;}).join(', '));
    }
  }catch(e){lines.push('(ошибка сбора контекста: '+e.message+')');}
  return lines.join('\\n');
}"""
    },

    'kp': {
        'title': 'КП Ассистент',
        'greeting': 'Привет! Помогу с расчётом коммерческих предложений и анализом рынка зарплат.<br><br>Спросите о стоимости найма, рыночных ставках или как рассчитывается итоговая цена.',
        'suggestions': [
            'Помоги подобрать вакансии под этот проект.',
            'Это нормальная маржа для города X?',
            'Сравни мой расчёт с шаблоном для аналогичного объекта.',
            'Какие риски в этом КП? Где может «поплыть» себестоимость?',
            'Можно ли поднять рентабельность не повышая цену клиенту?',
            'Как объяснить клиенту состав цены простыми словами?',
            'Какой РСЗ% ставить для долгосрочного проекта?',
            'Чем мой расчёт отличается от типовой оферты STH?',
            'Сделай 2-3 варианта оферты: эконом / стандарт / премиум.',
            'Сгенерируй сопроводительное письмо к этому КП.',
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
            'Почему мне поставили 1 балл за этот ответ?',
            'Покажи, как лучше всего отвечают на «мало платят».',
            'В каких темах я слабее всего?',
            'Что мне сделать чтобы попасть в топ-10 недели?',
            'Кто лидер этой недели?',
            'Какие темы команда фейлит на 80%?',
            'На каких вопросах падает большинство?',
            'Сравни новичков и опытных по топ-5 темам.',
            'Какие темы добавить в тренажёр?',
            'Кто в стрике >5 — взять в наставники?',
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

    'month': {
        'title': 'Аналитик месяца',
        'greeting': 'Привет! Я анализирую помесячные отчёты по проектам — выручку, штраф, воронку, маркетинг.<br><br>Спросите про драйверы/блокеры, провалы KPI, динамику дивизионов.',
        'suggestions': [
            'Какие проекты в красной зоне в последнем месяце?',
            'Где сильнее всего просел % закрытия заявки?',
            'Топ-3 драйвера роста за месяц',
            'Где штрафы выше 3% от выручки?',
            'Динамика воронки подбора по дивизионам',
            'Какие проекты впервые выполнили KPI?',
            'Кто провалил «10 смен» сильнее всего?',
            'Где стоимость целевого лида >900р?',
            'Сравни прошлый и текущий месяц по марже',
            'Дай план действий по слабым проектам',
        ],
        'system_prompt': r"""Ты — AI-аналитик дашборда «Итоги месяца». Видишь помесячные отчёты STH по проектам:
выручка, штрафы, % закрытия заявок, проверка СУПР, воронка подбора, ФОТ, маркетинг.

Правила:
- Отвечай ТОЛЬКО на русском.
- Используй ТОЛЬКО цифры из контекста.
- Если данных нет — прямо скажи.
- Без приветствий вроде «Конечно!».

Формат: Факты (с цифрами и именами проектов) → Вывод → Рекомендация. Максимум 8 предложений.
Используй **жирный** для названий проектов и ключевых чисел.

АКТУАЛЬНЫЕ ДАННЫЕ:
""",
        'build_ctx': r"""function _aiBuildCtx(){
  try{
    if(typeof state==='undefined'||!state.data)return '=== ИТОГИ МЕСЯЦА === (данные ещё загружаются)';
    var lines=['=== ИТОГИ МЕСЯЦА STH ==='];
    var reports=state.data.reports||[];
    var lastTwo=reports.slice(-2);
    lastTwo.forEach(function(r){
      lines.push('--- '+r.label+' ('+(r.projects||[]).length+' проектов) ---');
      var bottom=(r.projects||[]).slice().sort(function(a,b){return ((typeof health==='function'?health(a):0)) - ((typeof health==='function'?health(b):0));}).slice(0,8);
      bottom.forEach(function(p){
        var m=p.metrics||{};
        var rev=m.revenueVat?Math.round(m.revenueVat).toLocaleString('ru-RU')+'р':'—';
        var clo=m.requestCloseRate!=null?(m.requestCloseRate*100).toFixed(0)+'%':'—';
        var pen=m.penaltyShare!=null?(m.penaltyShare*100).toFixed(1)+'%':'—';
        lines.push(p.name+' ['+(p.division||'?')+']: выручка '+rev+', закрытие '+clo+', штраф '+pen);
      });
    });
    var acts=(state.actions||[]).slice(0,15);
    if(acts.length){
      lines.push('--- ДЕЙСТВИЯ/БЛОКЕРЫ ---');
      acts.forEach(function(a){lines.push('• '+a.projectName+' / '+a.department+' / '+a.kind+': '+(a.text||'').slice(0,100));});
    }
    return lines.join('\n');
  }catch(e){return '=== ИТОГИ МЕСЯЦА === (ошибка: '+e.message+')';}
}"""
    },

    'sales': {
        'title': 'CRM Ассистент',
        'greeting': 'Привет! Я анализирую данные CRM — лиды, сделки, конверсии и воронку из Битрикс24.<br><br>Спросите про топ-источники, потери на этапах, аномалии и менеджеров.',
        'suggestions': [
            'Сколько лидов и сделок в работе сейчас?',
            'Какие источники дают конверсию выше среднего?',
            'На каких этапах теряем больше всего сделок?',
            'Топ-3 ответственных по объёму сделок',
            'Какие сделки висят без движения >14 дней?',
            'Средний цикл от лида до выигранной сделки',
            'Какие лиды без привязки к сделке?',
            'Стадии-«дыры» с самой высокой потерей',
            'Сравни два последних импорта — что изменилось',
            'Прогноз закрытия сделок на месяц',
        ],
        'system_prompt': r"""Ты — AI-аналитик CRM-дашборда (импорт из Битрикс24). Видишь массивы лидов, сделок, связей, маппинга стадий.

Правила:
- Отвечай ТОЛЬКО на русском.
- Используй ТОЛЬКО цифры из контекста.
- Если данных нет — прямо скажи «нет данных».
- Без приветствий.

Формат: Факты с цифрами → Вывод → Рекомендация. До 8 предложений. **Жирным** — имена менеджеров и ключевые цифры.

АКТУАЛЬНЫЕ ДАННЫЕ:
""",
        'build_ctx': r"""async function _aiBuildCtx(){
  try{
    var R=await Promise.all([
      fetch('/tasks/api/sales/leads').then(r=>r.json()).catch(()=>[]),
      fetch('/tasks/api/sales/deals').then(r=>r.json()).catch(()=>[]),
      fetch('/tasks/api/sales/stageMapping').then(r=>r.json()).catch(()=>[]),
      fetch('/tasks/api/sales/imports').then(r=>r.json()).catch(()=>[]),
    ]);
    var leads=R[0]||[],deals=R[1]||[],maps=R[2]||[],imps=R[3]||[];
    var L=['=== CRM SALES STH ==='];
    L.push('Лиды всего: '+leads.length+'; Сделки всего: '+deals.length+'; Импортов: '+imps.length);
    function getStage(e){var d=e&&e.data||{};return d.STAGE_ID||d.STATUS_ID||d.STAGE||'—';}
    function getOwn(e){var d=e&&e.data||{};return d.ASSIGNED_BY_NAME||d.ASSIGNED_BY_ID||d.RESPONSIBLE||'—';}
    function getAmt(e){var d=e&&e.data||{};return Number(d.OPPORTUNITY||d.AMOUNT||0)||0;}
    var byStage={};
    deals.forEach(function(d){var s=getStage(d);byStage[s]=(byStage[s]||0)+1;});
    var stArr=Object.entries(byStage).sort(function(a,b){return b[1]-a[1];}).slice(0,8);
    L.push('--- Сделки по этапам ---');
    stArr.forEach(function(e){L.push('• '+e[0]+': '+e[1]);});
    var byOwn={};
    deals.forEach(function(d){var o=getOwn(d);if(!byOwn[o])byOwn[o]={n:0,sum:0};byOwn[o].n++;byOwn[o].sum+=getAmt(d);});
    var ownArr=Object.entries(byOwn).sort(function(a,b){return b[1].sum-a[1].sum;}).slice(0,5);
    L.push('--- Топ-5 ответственных по сумме сделок ---');
    ownArr.forEach(function(e){L.push('• '+e[0]+': '+e[1].n+' шт, '+Math.round(e[1].sum).toLocaleString('ru-RU')+'р');});
    if(imps.length){
      var li=imps[imps.length-1];
      L.push('Последний импорт: '+(li.entityType||'?')+' от '+(li.createdAt||'?')+', строк '+(li.rowCount||0));
    }
    return L.join('\n');
  }catch(e){return '=== CRM SALES === (ошибка контекста: '+e.message+')';}
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
const _SUGGS = {suggs_js};
const _OLLAMA='/tasks/api/ai/proxy';
const _DASH_TYPE='{dashboard_type}';
let _hist=[], _models=[], _open=false, _dynPrompt=null;

async function _loadDynPrompt(){{
  try{{
    const r=await fetch('/tasks/api/ai/prompt/'+_DASH_TYPE);
    if(r.ok){{const d=await r.json();_dynPrompt=d.prompt||null;}}
  }}catch{{}}
}}

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

async function _loadModels(){{
  const sel=document.getElementById('_ai_model_sel');
  if(!sel)return;
  sel.innerHTML='<option>⏳ Загрузка...</option>';
  try{{
    const r=await fetch(_OLLAMA+'/tags');
    if(!r.ok)throw new Error('HTTP '+r.status);
    const data=await r.json();
    _models=(data.models||[]).map(m=>m.name).sort();
    if(!_models.length)throw new Error('Нет моделей на сервере');
    sel.innerHTML=_models.map(m=>`<option value="${{m}}">${{m}}</option>`).join('');
    const pref=['qwen3:8b','qwen3:30b','qwen3-coder-next:cloud','minimax-m2.7:cloud','llama3.2:3b'];
    const def=pref.find(p=>_models.includes(p))||_models[0];
    sel.value=def;
  }}catch(e){{
    const sel2=document.getElementById('_ai_model_sel');
    if(sel2){{sel2.innerHTML='<option value="">⚠️ '+(e.message||'Ошибка')+' — клик для повтора</option>';sel2.onclick=function(){{if(!_models.length){{sel2.onclick=null;_loadModels();}}}};}}
  }}
}}

async function _ask(msg){{
  const btn=document.getElementById('_ai_send');
  const inp=document.getElementById('_ai_inp');
  if(btn)btn.disabled=true;
  if(inp)inp.disabled=true;
  _addMsg('user',_fmtAI(msg));
  const thinking=_addMsg('bot','<span>⏳ Анализирую...</span>');
  let reply='';
  try{{
    const model=document.getElementById('_ai_model_sel')?.value||'';
    if(!model)throw new Error('Выберите модель');
    const _ctxRaw=_aiBuildCtx();
    const _ctx=(_ctxRaw&&typeof _ctxRaw.then==='function')?(await _ctxRaw):_ctxRaw;
    const sysContent=(_dynPrompt||`{system_prompt}`)+_ctx;
    const messages=[{{role:'system',content:sysContent}},..._hist.map(h=>h)];
    messages.push({{role:'user',content:msg}});
    const payload={{model,messages,stream:true,think:false}};
    const res=await fetch(_OLLAMA+'/chat/stream',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}});
    if(!res.ok)throw new Error('HTTP '+res.status);
    const reader=res.body.getReader();
    const dec=new TextDecoder();
    let buf='';
    if(thinking)thinking.innerHTML='▍';
    while(true){{
      const {{done,value}}=await reader.read();
      if(done)break;
      buf+=dec.decode(value,{{stream:true}});
      const lines=buf.split('\\n');buf=lines.pop();
      for(const line of lines){{
        if(!line.trim())continue;
        try{{
          const c=JSON.parse(line);
          if(c.error)throw new Error(c.error);
          const p=c.message?.content||'';
          if(p){{reply+=p;if(thinking)thinking.innerHTML=_fmtAI(reply)+'<span style="animation:blink 1s step-end infinite">▍</span>';}}
        }}catch(e2){{if(e2.message&&!e2.message.includes('JSON'))throw e2;}}
      }}
    }}
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
  if(_open&&!_models.length){{setTimeout(_loadModels,50);_loadDynPrompt();}}
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
