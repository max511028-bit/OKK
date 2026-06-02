#!/usr/bin/env python3
"""
Делает три вещи одним проходом:

1. Инжектит floating "?" кнопку + модалку на все 8 дашбордов
   (okk, wb, finance, kp, tasks, hr-game, recruiter, sales).
   Кнопка тянет markdown из /docs/<dashboard>/help.md и рендерит в модалке.

2. Дополнительно для Финансов инжектит "📥 Excel" кнопку рядом с "?":
   выгружает все видимые таблицы текущей вкладки в .xlsx через SheetJS CDN.

3. Добавляет STH_MOBILE_CSS блок на admin.html, analyst/index.html, chat/index.html
   (на остальных он уже стоит).

Идемпотентность: каждая инъекция помечена комментарием-маркером — повторный прогон
не дублирует, а заменяет блок in-place.

Использование:
    python scripts/inject_help_excel_mobile.py
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---- 1. HELP BUTTON (на все дашборды) ----
HELP_START = "<!-- STH_HELP_BTN_START -->"
HELP_END = "<!-- STH_HELP_BTN_END -->"

HELP_BLOCK_TEMPLATE = """
<!-- STH_HELP_BTN_START -->
<style>
#_sth_help_btn{{position:fixed;bottom:24px;right:90px;width:44px;height:44px;border-radius:50%;
  background:#1f2937;color:#fff;border:1px solid rgba(255,255,255,.15);font-size:20px;font-weight:700;
  cursor:pointer;z-index:9996;box-shadow:0 4px 16px rgba(0,0,0,.4);
  display:flex;align-items:center;justify-content:center;transition:transform .15s,background .15s}}
#_sth_help_btn:hover{{background:#374151;transform:scale(1.08)}}
#_sth_help_modal{{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:99998;display:none;
  align-items:center;justify-content:center;padding:20px;backdrop-filter:blur(4px)}}
#_sth_help_modal.show{{display:flex}}
#_sth_help_box{{background:#161b22;color:#e6edf3;border:1px solid rgba(255,255,255,.1);
  border-radius:14px;max-width:680px;width:100%;max-height:80vh;overflow:auto;
  padding:24px 28px;box-shadow:0 24px 80px rgba(0,0,0,.7);font:14px/1.6 Inter,system-ui,sans-serif}}
#_sth_help_box h1{{font-size:22px;margin:0 0 14px;color:#fff}}
#_sth_help_box h2{{font-size:16px;margin:18px 0 8px;color:#fff}}
#_sth_help_box h3{{font-size:14px;margin:14px 0 6px;color:#fff}}
#_sth_help_box p{{margin:0 0 10px;color:#cbd5e1}}
#_sth_help_box ul,#_sth_help_box ol{{margin:0 0 10px;padding-left:22px;color:#cbd5e1}}
#_sth_help_box li{{margin-bottom:4px}}
#_sth_help_box code{{background:rgba(255,255,255,.08);padding:1px 6px;border-radius:4px;font-size:12px}}
#_sth_help_box strong{{color:#fff}}
#_sth_help_close{{float:right;background:transparent;border:0;color:#94a3b8;font-size:22px;cursor:pointer;
  margin:-8px -8px 0 0;padding:4px 10px;border-radius:6px}}
#_sth_help_close:hover{{background:rgba(255,255,255,.08);color:#fff}}
@media(max-width:640px){{#_sth_help_btn{{bottom:18px;right:78px;width:40px;height:40px}}#_sth_help_box{{padding:18px 16px}}}}
</style>
<button id="_sth_help_btn" title="Помощь по дашборду">?</button>
<div id="_sth_help_modal"><div id="_sth_help_box"><button id="_sth_help_close">×</button><div id="_sth_help_content">Загрузка…</div></div></div>
<script>
(function(){{
  const SLUG='{slug}';
  const btn=document.getElementById('_sth_help_btn');
  const modal=document.getElementById('_sth_help_modal');
  const close=document.getElementById('_sth_help_close');
  const box=document.getElementById('_sth_help_box');
  const content=document.getElementById('_sth_help_content');
  let _loaded=false;
  function mdToHtml(md){{
    // Минимальный markdown → HTML рендер. Без зависимостей.
    md=md.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    md=md.replace(/^### (.+)$/gm,'<h3>$1</h3>');
    md=md.replace(/^## (.+)$/gm,'<h2>$1</h2>');
    md=md.replace(/^# (.+)$/gm,'<h1>$1</h1>');
    md=md.replace(/\\*\\*(.+?)\\*\\*/g,'<strong>$1</strong>');
    md=md.replace(/`([^`]+)`/g,'<code>$1</code>');
    // списки
    md=md.replace(/(^|\\n)((?:- .+(?:\\n|$))+)/g,function(m,p,b){{
      const items=b.trim().split('\\n').map(l=>'<li>'+l.replace(/^- /,'')+'</li>').join('');
      return p+'<ul>'+items+'</ul>';
    }});
    md=md.replace(/(^|\\n)((?:\\d+\\. .+(?:\\n|$))+)/g,function(m,p,b){{
      const items=b.trim().split('\\n').map(l=>'<li>'+l.replace(/^\\d+\\. /,'')+'</li>').join('');
      return p+'<ol>'+items+'</ol>';
    }});
    // параграфы
    return md.split(/\\n\\n+/).map(b=>b.match(/^<(h\\d|ul|ol|li|p)/) ? b : ('<p>'+b.replace(/\\n/g,'<br>')+'</p>')).join('\\n');
  }}
  async function loadHelp(){{
    if(_loaded)return;
    try{{
      const r=await fetch('/docs/'+SLUG+'/help.md?_='+Date.now());
      if(!r.ok)throw new Error('HTTP '+r.status);
      const md=await r.text();
      content.innerHTML=mdToHtml(md);
      _loaded=true;
    }}catch(e){{content.innerHTML='<p style="color:#f87171">Не удалось загрузить help.md: '+e.message+'</p>';}}
  }}
  btn.addEventListener('click',function(){{modal.classList.add('show');loadHelp();}});
  close.addEventListener('click',function(){{modal.classList.remove('show');}});
  modal.addEventListener('click',function(e){{if(e.target===modal)modal.classList.remove('show');}});
  document.addEventListener('keydown',function(e){{if(e.key==='Escape')modal.classList.remove('show');}});
}})();
</script>
<!-- STH_HELP_BTN_END -->
"""

# ---- 2. EXCEL EXPORT (только финансы) ----
EXCEL_START = "<!-- STH_EXCEL_BTN_START -->"
EXCEL_END = "<!-- STH_EXCEL_BTN_END -->"

EXCEL_BLOCK = """
<!-- STH_EXCEL_BTN_START -->
<style>
#_sth_xlsx_btn{position:fixed;bottom:24px;right:150px;width:44px;height:44px;border-radius:50%;
  background:#0d6e3f;color:#fff;border:1px solid rgba(255,255,255,.15);font-size:18px;
  cursor:pointer;z-index:9996;box-shadow:0 4px 16px rgba(0,0,0,.4);
  display:flex;align-items:center;justify-content:center;transition:transform .15s,background .15s}
#_sth_xlsx_btn:hover{background:#0fa44d;transform:scale(1.08)}
@media(max-width:640px){#_sth_xlsx_btn{bottom:18px;right:138px;width:40px;height:40px}}
</style>
<button id="_sth_xlsx_btn" title="Выгрузить текущую вкладку в Excel">📥</button>
<script src="https://cdn.jsdelivr.net/npm/xlsx@0.18.5/dist/xlsx.full.min.js"></script>
<script>
(function(){
  const btn=document.getElementById('_sth_xlsx_btn');
  btn.addEventListener('click',function(){
    if(typeof XLSX==='undefined'){alert('SheetJS не загрузился');return;}
    const tab=document.querySelector('.tab-content.active');
    if(!tab){alert('Нет активной вкладки');return;}
    const tables=tab.querySelectorAll('table');
    if(!tables.length){alert('На этой вкладке нет таблиц для выгрузки');return;}
    const wb=XLSX.utils.book_new();
    let i=0;
    tables.forEach(function(tbl){
      try{
        const ws=XLSX.utils.table_to_sheet(tbl,{raw:false});
        let name=(tbl.getAttribute('data-name')||tbl.closest('[data-sheet]')?.getAttribute('data-sheet')||'Sheet'+(++i)).slice(0,28);
        // SheetJS требует уникальные имена листов
        let uniq=name,k=2;
        while(wb.SheetNames.includes(uniq)){uniq=(name+'_'+(k++)).slice(0,31);}
        XLSX.utils.book_append_sheet(wb,ws,uniq);
      }catch(e){console.warn('skip table',e);}
    });
    if(!wb.SheetNames.length){alert('Не удалось преобразовать таблицы');return;}
    const tabName=(document.querySelector('.tab-btn.active')?.textContent||'finance').trim().replace(/[^\\wа-яА-Я]+/g,'_');
    const ts=new Date().toISOString().slice(0,10);
    XLSX.writeFile(wb,'STH_'+tabName+'_'+ts+'.xlsx');
  });
})();
</script>
<!-- STH_EXCEL_BTN_END -->
"""

# ---- 3. MOBILE CSS (для admin/analyst/chat) ----
MOBILE_START = "<!-- STH_MOBILE_CSS_START -->"
MOBILE_END = "<!-- STH_MOBILE_CSS_END -->"

MOBILE_BLOCK = """
<!-- STH_MOBILE_CSS_START -->
<style id="_sth_mobile_overrides">
@media (max-width: 768px) {
  html, body { font-size: 14px; }
  [class*="grid"], [class*="-grid"] { grid-template-columns: 1fr !important; gap: 12px !important; }
  table { display: block; overflow-x: auto; -webkit-overflow-scrolling: touch; white-space: nowrap; max-width: 100%; }
  .container, main, .main, .content, .page-content { padding-left: 12px !important; padding-right: 12px !important; }
  h1 { font-size: 22px !important; line-height: 1.25; }
  h2 { font-size: 18px !important; line-height: 1.3; }
  h3 { font-size: 16px !important; }
  aside, .sidebar, nav.side, .side-nav { position: static !important; width: 100% !important; height: auto !important; border-right: 0 !important; border-bottom: 1px solid rgba(255,255,255,.08); }
  button, .btn, [role="button"] { min-height: 40px; }
  .card, .kpi, .metric, .stat-card, .stat { width: 100% !important; min-width: 0 !important; }
  canvas, svg, .chart, [class*="chart"] { max-width: 100% !important; height: auto !important; }
}
@media (max-width: 480px) {
  html, body { font-size: 13px; }
  h1 { font-size: 19px !important; }
  h2 { font-size: 16px !important; }
  #_ai_panel, #_tai_panel { width: calc(100vw - 16px) !important; height: calc(100vh - 100px) !important; bottom: 80px !important; right: 8px !important; left: 8px !important; max-width: none !important; max-height: none !important; }
  #_ai_btn, #_tai_btn { bottom: 16px !important; right: 16px !important; width: 50px !important; height: 50px !important; }
  #_sth_help_btn { right: 78px !important; }
  #_sth_xlsx_btn { right: 138px !important; }
  #_sth_ai_offline_banner { font-size: 11px !important; padding: 6px 10px !important; }
  .subtitle, .desc-long, .label-decoration { display: none; }
  .container, main, .main, .content, .page-content, .panel, .section, section { padding: 8px !important; }
}
@media (max-width: 1024px) {
  .table-wrap, .table-container { overflow-x: auto; -webkit-overflow-scrolling: touch; }
}
</style>
<!-- STH_MOBILE_CSS_END -->
"""


def inject_between_markers(text: str, start: str, end: str, block: str) -> tuple[str, bool]:
    """Заменяет [start ... end] на новый block. Возвращает (новый_text, было_ли_изменение)."""
    pattern = re.compile(re.escape(start) + ".*?" + re.escape(end), re.DOTALL)
    if pattern.search(text):
        new_text = pattern.sub(block.strip(), text)
        return new_text, new_text != text
    # Иначе — вставляем перед </body>
    if "</body>" in text:
        new_text = text.replace("</body>", block.strip() + "\n</body>", 1)
        return new_text, True
    # последняя надежда — в конец
    return text + "\n" + block.strip(), True


DASHBOARDS = [
    ("okk", "okk/index.html"),
    ("wb", "wb/index.html"),
    ("finance", "finance/index.html"),
    ("kp", "kp/index.html"),
    ("tasks", "tasks/index.html"),
    ("hr-game", "hr-game/index.html"),
    ("recruiter", "recruiter/index.html"),
    ("sales", "sales/index.html"),
]

MOBILE_TARGETS = [
    "admin.html",
    "analyst/index.html",
    "chat/index.html",
]


def main():
    changed = []
    # 1+2: help button (+ excel для финансов) на все dashboards
    for slug, rel in DASHBOARDS:
        p = ROOT / rel
        if not p.exists():
            print(f"  ! skip {rel}: not found")
            continue
        text = p.read_text(encoding="utf-8")
        block = HELP_BLOCK_TEMPLATE.format(slug=slug)
        new_text, ch1 = inject_between_markers(text, HELP_START, HELP_END, block)
        ch2 = False
        if slug == "finance":
            new_text, ch2 = inject_between_markers(new_text, EXCEL_START, EXCEL_END, EXCEL_BLOCK)
        if ch1 or ch2:
            p.write_text(new_text, encoding="utf-8")
            tags = []
            if ch1:
                tags.append("help")
            if ch2:
                tags.append("excel")
            changed.append(f"{rel}: {'+'.join(tags)}")

    # 3: mobile CSS на admin/analyst/chat
    for rel in MOBILE_TARGETS:
        p = ROOT / rel
        if not p.exists():
            print(f"  ! skip {rel}: not found")
            continue
        text = p.read_text(encoding="utf-8")
        if MOBILE_START in text:
            print(f"  = {rel}: mobile уже стоит, обновляю")
        new_text, ch = inject_between_markers(text, MOBILE_START, MOBILE_END, MOBILE_BLOCK)
        if ch:
            p.write_text(new_text, encoding="utf-8")
            changed.append(f"{rel}: mobile")

    if not changed:
        print("ничего не изменилось")
    else:
        print(f"OK, обновлено {len(changed)} файлов:")
        for c in changed:
            print(f"  • {c}")


if __name__ == "__main__":
    main()
