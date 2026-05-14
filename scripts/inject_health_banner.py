#!/usr/bin/env python3
"""
Внедряет в HTML глобальную плашку «ИИ временно недоступен».
Опрашивает /tasks/api/ai/health каждые 60 секунд.

Usage:
    python scripts/inject_health_banner.py <file.html> [<file.html> ...]
"""
import sys
import re

MARK_START = '<!-- STH_AI_HEALTH_BANNER_START -->'
MARK_END = '<!-- STH_AI_HEALTH_BANNER_END -->'

SNIPPET = f"""
{MARK_START}
<style>
#_sth_ai_offline_banner {{
  position: fixed; top: 0; left: 0; right: 0;
  background: #b91c1c; color: #fff;
  padding: 8px 16px; text-align: center;
  font: 600 13px/1.4 Inter, system-ui, sans-serif;
  z-index: 99999; display: none;
  box-shadow: 0 2px 8px rgba(0,0,0,.35);
}}
#_sth_ai_offline_banner.show {{ display: block; }}
</style>
<div id="_sth_ai_offline_banner">⚠️ ИИ временно недоступен — отвечать не сможет. Команда уже видит проблему.</div>
<script>
(function() {{
  const URL = '/tasks/api/ai/health';
  const EL = () => document.getElementById('_sth_ai_offline_banner');
  let _lastOnline = null;
  async function _check() {{
    try {{
      const r = await fetch(URL, {{ cache: 'no-store' }});
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      const online = !!d.online;
      const el = EL();
      if (el) el.classList.toggle('show', !online);
      _lastOnline = online;
    }} catch (e) {{
      const el = EL();
      if (el) el.classList.add('show');
    }}
  }}
  // Первая проверка через 2 сек после загрузки, далее раз в 60 сек
  document.addEventListener('DOMContentLoaded', function() {{
    setTimeout(_check, 2000);
    setInterval(_check, 60000);
  }});
}})();
</script>
{MARK_END}
"""


def inject(filename: str) -> bool:
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()
    # Удалить предыдущую инъекцию, если есть
    content = re.sub(
        re.escape(MARK_START) + r'.*?' + re.escape(MARK_END) + r'\s*',
        '',
        content,
        flags=re.DOTALL,
    )
    if '</body>' not in content:
        print(f'  ! no </body> in {filename}')
        return False
    content = content.replace('</body>', SNIPPET + '\n</body>', 1)
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'  ok: banner injected into {filename}')
    return True


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python inject_health_banner.py <file.html> [...]')
        sys.exit(1)
    for fn in sys.argv[1:]:
        try:
            inject(fn)
        except Exception as e:
            print(f'  ERR {fn}: {e}')
