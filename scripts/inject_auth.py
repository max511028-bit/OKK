#!/usr/bin/env python3
"""Injects password protection overlay into an HTML file after <body> tag."""
import sys

AUTH_SCRIPT = """<script>
(function(){
  var H='7c36cbe784c6442e1467a8e1ab61a2dbb7836de8ad882a210c21be8d35290917',K='p_auth';
  if(sessionStorage.getItem(K)===H)return;
  document.addEventListener('DOMContentLoaded',function(){
    var o=document.createElement('div');
    o.id='_pov';
    o.style.cssText='position:fixed;inset:0;background:#0d1117;display:flex;align-items:center;justify-content:center;z-index:2147483647;font-family:Inter,system-ui,sans-serif';
    o.innerHTML='<div style="background:#161b22;border:1px solid rgba(255,255,255,.1);border-radius:20px;padding:44px 40px;width:360px;text-align:center;box-shadow:0 24px 80px rgba(0,0,0,.6)">'
      +'<div style="font-size:42px;margin-bottom:18px">\uD83D\uDD12</div>'
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
      crypto.subtle.digest('SHA-256',new TextEncoder().encode(v)).then(function(buf){
        var h=[].slice.call(new Uint8Array(buf)).map(function(b){return b.toString(16).padStart(2,'0');}).join('');
        if(h===H){sessionStorage.setItem(K,H);document.getElementById('_pov').remove();}
        else{document.getElementById('_pe').textContent='\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u043f\u0430\u0440\u043e\u043b\u044c';document.getElementById('_pi').value='';}
      });
    });
  });
})();
</script>"""

def inject(filename):
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()
    if 'p_auth' in content:
        print(f"Auth already present in {filename}, skipping")
        return
    if '<body>' not in content:
        print(f"WARNING: no <body> tag found in {filename}")
        return
    content = content.replace('<body>', '<body>\n' + AUTH_SCRIPT, 1)
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Auth injected into {filename}")

if __name__ == '__main__':
    for path in sys.argv[1:]:
        inject(path)
