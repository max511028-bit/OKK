#!/usr/bin/env python3
"""
Inject/remove password gate.

Использование:
    python inject_auth.py inject  <file1.html> [<file2.html> ...]
    python inject_auth.py remove  <file1.html> [<file2.html> ...]

Пароль НЕ зашит в клиент — проверка идёт серверным эндпоинтом
POST /tasks/api/auth/check.  Клиент только хранит opaque-токен
после успешного входа в sessionStorage.
"""
import sys, re

AUTH_SCRIPT = '''<script>
(function(){
  var K='p_auth';
  if(sessionStorage.getItem(K))return;
  document.addEventListener('DOMContentLoaded',function(){
    var o=document.createElement('div');
    o.id='_pov';
    o.style.cssText='position:fixed;inset:0;background:#0d1117;display:flex;align-items:center;justify-content:center;z-index:2147483647;font-family:Inter,system-ui,sans-serif';
    o.innerHTML='<div style="background:#161b22;border:1px solid rgba(255,255,255,.1);border-radius:20px;padding:44px 40px;width:360px;text-align:center;box-shadow:0 24px 80px rgba(0,0,0,.6)">'
      +'<div style="font-size:42px;margin-bottom:18px">\U0001F512</div>'
      +'<div style="font-size:22px;font-weight:700;color:#f0f6fc;margin-bottom:8px">\u0414\u043e\u0441\u0442\u0443\u043f \u0437\u0430\u043a\u0440\u044b\u0442</div>'
      +'<div style="font-size:14px;color:#8b949e;margin-bottom:28px">\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043f\u0430\u0440\u043e\u043b\u044c \u0434\u043b\u044f \u0432\u0445\u043e\u0434\u0430</div>'
      +'<input id="_pi" type="password" placeholder="\u041f\u0430\u0440\u043e\u043b\u044c" style="width:100%;padding:13px 16px;background:#0d1117;border:1px solid rgba(255,255,255,.15);border-radius:10px;color:#f0f6fc;font-size:15px;outline:none;margin-bottom:10px;box-sizing:border-box" />'
      +'<div id="_pe" style="color:#f85149;font-size:13px;margin-bottom:12px;min-height:18px"></div>'
      +'<button id="_pb" style="width:100%;padding:13px;background:#238636;border:none;border-radius:10px;color:#fff;font-size:15px;font-weight:600;cursor:pointer">\u0412\u043e\u0439\u0442\u0438</button>'
      +'</div>';
    document.body.appendChild(o);
    setTimeout(function(){var pi=document.getElementById('_pi'); if(pi) pi.focus();},80);
    function submit(){
      var pi=document.getElementById('_pi'); var pe=document.getElementById('_pe'); var pb=document.getElementById('_pb');
      var v=pi.value; if(!v) return;
      pb.disabled=true; pb.textContent='\u2026';
      fetch('/tasks/api/auth/check',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({password:v,kind:'portal'})
      }).then(function(r){
        if(r.ok) return r.json();
        throw new Error(r.status===403?'\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u043f\u0430\u0440\u043e\u043b\u044c':'\u041e\u0448\u0438\u0431\u043a\u0430 '+r.status);
      }).then(function(d){
        if(d&&d.token){sessionStorage.setItem(K,d.token); var ov=document.getElementById('_pov'); if(ov) ov.remove();}
        else throw new Error('\u041d\u0435\u0442 \u0442\u043e\u043a\u0435\u043d\u0430');
      }).catch(function(e){
        pe.textContent=e.message||'\u041e\u0448\u0438\u0431\u043a\u0430'; pi.value=''; pi.focus();
        pb.disabled=false; pb.textContent='\u0412\u043e\u0439\u0442\u0438';
      });
    }
    document.getElementById('_pi').addEventListener('keydown',function(e){if(e.key==='Enter')submit();});
    document.getElementById('_pb').addEventListener('click',submit);
  });
})();
</script>'''


# Regex'ы привязаны к уникальному маркеру K='p_auth' — чтобы случайно
# не съесть посторонний IIFE из чужого кода (это уже однажды стерло wb/index.html).
_AUTH_BLOCK_RE = re.compile(
    r"<script>\s*\(function\(\)\{[^<]*?K='p_auth'[\s\S]*?\}\)\(\);\s*</script>\n?",
)


def _strip(content: str) -> str:
    return _AUTH_BLOCK_RE.sub('', content)


def inject(filename: str) -> None:
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()
    content = _strip(content)
    if '<body>' not in content:
        print(f'WARNING: no <body> tag in {filename}')
        return
    content = content.replace('<body>', '<body>\n' + AUTH_SCRIPT, 1)
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'[INJECT] {filename}')


def remove(filename: str) -> None:
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()
    new = _strip(content)
    if new == content:
        print(f'[SKIP]   {filename} (no auth block found)')
        return
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(new)
    print(f'[REMOVE] {filename}')


if __name__ == '__main__':
    if len(sys.argv) < 3 or sys.argv[1] not in ('inject', 'remove'):
        print(__doc__)
        sys.exit(1)
    action = sys.argv[1]
    fn = inject if action == 'inject' else remove
    for path in sys.argv[2:]:
        fn(path)
