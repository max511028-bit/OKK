#!/usr/bin/env python3
"""
Внедряет в HTML общий mobile-responsive CSS-блок (медиа-запросы под телефоны).

Универсальный @media (max-width: 768px) и (max-width: 480px) с правилами:
- таблицы → горизонтальный скролл
- любые grid с repeat(N,...) → принудительно 1 колонка
- sidebar/nav → стек, мелкий шрифт
- AI-панель → fullscreen
- общее уменьшение шрифтов / padding

Идемпотентен — повторный запуск переписывает блок.

Usage:
    python scripts/inject_mobile.py <file.html> [<file.html> ...]
"""
import sys
import re

MARK_START = '<!-- STH_MOBILE_CSS_START -->'
MARK_END = '<!-- STH_MOBILE_CSS_END -->'

CSS = f"""{MARK_START}
<style id="_sth_mobile_overrides">
/* ============ STH GLOBAL MOBILE OVERRIDES ============ */
@media (max-width: 768px) {{
  html, body {{ font-size: 14px; }}
  /* Любой grid с >=2 колонками → 1 колонка */
  [class*="grid"], [class*="-grid"] {{
    grid-template-columns: 1fr !important;
    gap: 12px !important;
  }}
  /* Таблицы — горизонтальный скролл через обёртку, либо сама таблица */
  table {{
    display: block;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    white-space: nowrap;
    max-width: 100%;
  }}
  /* Большие падинги/маргины ужать */
  .container, main, .main, .content, .page-content {{
    padding-left: 12px !important;
    padding-right: 12px !important;
  }}
  /* Заголовки тише */
  h1 {{ font-size: 22px !important; line-height: 1.25; }}
  h2 {{ font-size: 18px !important; line-height: 1.3; }}
  h3 {{ font-size: 16px !important; }}
  /* Сайдбар/навигация — на всю ширину сверху */
  aside, .sidebar, nav.side, .side-nav {{
    position: static !important;
    width: 100% !important;
    height: auto !important;
    border-right: 0 !important;
    border-bottom: 1px solid rgba(255,255,255,.08);
  }}
  /* Кнопки потолще для пальцев */
  button, .btn, [role="button"] {{
    min-height: 40px;
  }}
  /* Карточки KPI — на всю ширину */
  .card, .kpi, .metric, .stat-card, .stat {{
    width: 100% !important;
    min-width: 0 !important;
  }}
  /* Графики — никогда не вылезают за экран */
  canvas, svg, .chart, [class*="chart"] {{
    max-width: 100% !important;
    height: auto !important;
  }}
}}

@media (max-width: 480px) {{
  html, body {{ font-size: 13px; }}
  h1 {{ font-size: 19px !important; }}
  h2 {{ font-size: 16px !important; }}
  /* AI-панель — почти fullscreen */
  #_ai_panel, #_tai_panel {{
    width: calc(100vw - 16px) !important;
    height: calc(100vh - 100px) !important;
    bottom: 80px !important;
    right: 8px !important;
    left: 8px !important;
    max-width: none !important;
    max-height: none !important;
  }}
  #_ai_btn, #_tai_btn {{
    bottom: 16px !important;
    right: 16px !important;
    width: 50px !important;
    height: 50px !important;
  }}
  /* Плашка «ИИ оффлайн» — мельче чтоб не съедала пол-экрана */
  #_sth_ai_offline_banner {{
    font-size: 11px !important;
    padding: 6px 10px !important;
  }}
  /* Скрываем длинные subtitle/decoration на самых мелких */
  .subtitle, .desc-long, .label-decoration {{ display: none; }}
  /* Padding ещё уменьшаем */
  .container, main, .main, .content, .page-content,
  .panel, .section, section {{
    padding: 8px !important;
  }}
}}

/* На любых размерах если viewport узкий — таблицы прокручиваются */
@media (max-width: 1024px) {{
  .table-wrap, .table-container {{
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }}
}}
</style>
{MARK_END}
"""


def inject(filename: str) -> bool:
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()
    # Удалить предыдущую инъекцию
    content = re.sub(
        re.escape(MARK_START) + r'.*?' + re.escape(MARK_END) + r'\s*',
        '',
        content,
        flags=re.DOTALL,
    )
    # Убедиться что есть <meta viewport>
    if 'name="viewport"' not in content and 'name=\'viewport\'' not in content:
        viewport = '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        if '<head>' in content:
            content = content.replace('<head>', '<head>\n  ' + viewport, 1)
        elif '<HEAD>' in content:
            content = content.replace('<HEAD>', '<HEAD>\n  ' + viewport, 1)
    # Вставить CSS перед </head> (чтобы overrides шли последними и побеждали)
    if '</head>' in content:
        content = content.replace('</head>', CSS + '\n</head>', 1)
    elif '</HEAD>' in content:
        content = content.replace('</HEAD>', CSS + '\n</HEAD>', 1)
    else:
        print(f'  ! no </head> in {filename}')
        return False
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'  ok: mobile CSS injected into {filename}')
    return True


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python inject_mobile.py <file.html> [...]')
        sys.exit(1)
    for fn in sys.argv[1:]:
        try:
            inject(fn)
        except Exception as e:
            print(f'  ERR {fn}: {e}')
