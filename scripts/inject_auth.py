#!/usr/bin/env python3
"""Injects/replaces password protection overlay. Uses btoa (works on HTTP & HTTPS)."""
import sys, re

# btoa("511028") = "NTExMDI4"
AUTH_SCRIPT = '''<script>
(function(){
  var T='NTExMDI4',K='p_auth';
  if(sessionStorage.getItem(K)===T)return;
  document.addEventListener('DOMContentLoaded',function(){
    var o=document.createElement('div');
    o.id='_pov';
    o.style.cssText='position:fixed;inset:0;background:#0d1117;display:flex;align-items:center;justify-content:center;z-index:2147483647;font-family:Inter,system-ui,sans-serif';
    o.innerHTML='<div style="background:#161b22;border:1px solid rgba(255,255,255,.1);border-radius:20px;padding:44px 40px;width:360px;text-align:center;box-shadow:0 24px 80px rgba(0,0,0,.6)">'
      +'<div style="font-size:42px;margin-bottom:18px">🔒</div>'
      +'<div style="font-size:22px;font-weight:700;color:#f0f6fc;margin-bottom:8px">\u0414\u043e\u0441\u0442\u0443\u043f \u0437\u0430\u043a\u0440\u044b\u0442</div>'
      +'<div style="font-size:14px;color:#8b949e;margin-bottom:28px">\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043f\u0430\u0440\u043e\u043b\u044c \u0434\u043b\u044f \u0432\u0445\u043e\u0434\u0430</div>'
      +'<input id="_pi" type="password" placeholder="\u041f\u0430\u0440\u043e\u043b\u044c" style="width:100%;padding:13px 16px;background:#0d1117;border:1px solid rgba(255,255,255,.15);border-radius:10px;color:#f0f6fc;font-size:15px;outline:none;margin-bottom:10px;box-sizing:border-box" />'
      +'<div id="_pe" style="color:#f85149;font-size:13px;margin-bottom:12px;min-height:18px"></div>'
      +'<button id="_pb" style="width:100%;padding:13px;background:#238636;border:none;border-radius:10px;color:#fff;font-size:15px;font-weight:600;cursor:pointer">\u0412\u043e\u0439\u0442\u0438</button>'
      +'</div>';
    document.body.appendChild(o);
    setTimeout(function(){document.getElementById('_pi').focus();},80);
    document.getElementById('_pi').addEventListener('keydown',function(e){if(e.key==='Enter')document.getElementById('_pb').click();});
    document.getElementById('_pb').addEventListener('click',function(){
      var v=document.getElementById('_pi').value;
      try{
        if(btoa(v)===T){sessionStorage.setItem(K,T);document.getElementById('_pov').remove();}
        else{document.getElementById('_pe').textContent='\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u043f\u0430\u0440\u043e\u043b\u044c';document.getElementById('_pi').value='';}
      }catch(e){document.getElementById('_pe').textContent='Error: '+e.message;}
    });
  });
})();
</script>'''

def inject(filename):
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()

    # Remove old auth block if present (both old crypto.subtle version and old btoa version)
    content = re.sub(
        r'<script>\s*\(function\(\)\{\s*var [HT]=[\'"](7c36|NTEx)[^\n]*[\s\S]*?\}\)\(\);\s*</script>\n?',
        '', content, flags=re.MULTILINE
    )

    # Inject after <body>
    if '<body>' not in content:
        print(f'WARNING: no <body> tag in {filename}')
        return
    content = content.replace('<body>', '<body>\n' + AUTH_SCRIPT, 1)

    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'Auth injected into {filename}')

if __name__ == '__main__':
    for path in sys.argv[1:]:
        inject(path)
