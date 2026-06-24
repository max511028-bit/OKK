#!/bin/bash
# Server setup script: nginx proxy for AI Recruiter + Tasks API
# AI (Ollama) подключается к внешнему серверу 178.63.16.109:11434 — локальный не нужен
# Runs on VPS after rsync deploy

set -e
# Не прерываться на ошибках проверки внешних сервисов
trap 'echo "WARN: non-critical step failed, continuing..." >&2' ERR

SITE_CONF=""
for f in /etc/nginx/sites-enabled/*; do
  [ -f "$f" ] && SITE_CONF="$f" && break
done
if [ -z "$SITE_CONF" ]; then
  SITE_CONF="/etc/nginx/nginx.conf"
fi

# ── 1. Write Recruiter proxy snippet ──────────────────────────────────
# Ollama НЕ проксируется — дашборды обращаются напрямую к 178.63.16.109:11434
cat > /etc/nginx/snippets/portal-proxies.conf << 'NGINX_EOF'
# AI Recruiter (Streamlit on port 8501)
location /recruiter/ {
    proxy_pass http://127.0.0.1:8501/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 300s;
    rewrite ^/recruiter/(.*)$ /$1 break;
}

# Streamlit static assets
location /recruiter/_stcore/ {
    proxy_pass http://127.0.0.1:8501/_stcore/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}
NGINX_EOF

# ── 1b. Write SEPARATE tiny snippet for Задачник API (safe to include anywhere) ──
cat > /etc/nginx/snippets/tasks-api-proxy.conf << 'NGINX_EOF'
location /tasks/api/ {
    proxy_pass http://127.0.0.1:8601/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 60s;
    client_max_body_size 30m;     # для загрузки ТЗ-файлов до 25 МБ + запас
}
NGINX_EOF

# ── 2. Include snippet in EVERY server block of EVERY site config ─────
echo "=== Site configs found ==="
ls -la /etc/nginx/sites-enabled/ 2>/dev/null || true
ls -la /etc/nginx/conf.d/ 2>/dev/null || true

for cfg in /etc/nginx/sites-enabled/* /etc/nginx/conf.d/*.conf; do
  [ -f "$cfg" ] || continue
  python3 - "$cfg" << 'PYEOF'
import re, sys
path = sys.argv[1]
with open(path) as f:
    src = f.read()

# 1. Drop ALL existing portal-proxies / tasks-api-proxy includes (cleanup)
src = re.sub(r'^[ \t]*include\s+snippets/portal-proxies\.conf\s*;[ \t]*\n', '', src, flags=re.MULTILINE)
src = re.sub(r'^[ \t]*include\s+snippets/tasks-api-proxy\.conf\s*;[ \t]*\n', '', src, flags=re.MULTILINE)

# Helper to walk server blocks, returns list of (open_idx, close_idx, block_text)
def find_server_blocks(s):
    blocks = []
    i, n = 0, len(s)
    while i < n:
        m = re.search(r'\bserver\b\s*\{', s[i:])
        if not m: break
        abs_open = i + m.end() - 1
        depth, j = 1, abs_open + 1
        while j < n and depth > 0:
            c = s[j]
            if c == '{': depth += 1
            elif c == '}': depth -= 1
            j += 1
        blocks.append((abs_open, j-1, s[abs_open+1:j-1]))
        i = j
    return blocks

# 2. For each server block decide what to include:
#    - tasks-api-proxy.conf: ALWAYS (small, no conflicts) if listen + no existing /tasks/api/
#    - portal-proxies.conf: only if listen AND no existing /ollama/ (to avoid duplicates)
out = []
cursor = 0
tasks_inserted = portal_inserted = 0
for open_idx, close_idx, block in find_server_blocks(src):
    out.append(src[cursor:open_idx+1])  # text up to and including '{'
    uncomm = '\n'.join(re.sub(r'#.*$', '', line) for line in block.splitlines())
    has_listen = bool(re.search(r'\blisten\b', uncomm))
    has_ollama = bool(re.search(r'location\s+/ollama/', uncomm))
    has_tasks = bool(re.search(r'location\s+/tasks/api/', uncomm))
    extras = []
    if has_listen and not has_tasks:
        extras.append("    include snippets/tasks-api-proxy.conf;")
        tasks_inserted += 1
    if has_listen and not has_ollama:
        extras.append("    include snippets/portal-proxies.conf;")
        portal_inserted += 1
    if extras:
        out.append("\n" + "\n".join(extras))
    cursor = open_idx + 1
out.append(src[cursor:])

new = ''.join(out)
with open(path, 'w') as f:
    f.write(new)
print(f"[{path}] tasks-api: {tasks_inserted}, portal-proxies: {portal_inserted}")
PYEOF
done

echo "=== /etc/nginx/sites-enabled/default after modification ==="
cat /etc/nginx/sites-enabled/default 2>/dev/null | head -80 || true
echo "=== /etc/nginx/sites-enabled/okk after modification ==="
cat /etc/nginx/sites-enabled/okk 2>/dev/null | head -80 || true

# ── 3. Check external Ollama availability (некритично, не прерывает деплой) ──
echo "=== External Ollama check ==="
EXT=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 http://178.63.16.109:11434/api/tags 2>/dev/null || echo "ERR")
echo "  http://178.63.16.109:11434/api/tags → $EXT"
[ "$EXT" = "200" ] && echo "  ✓ External Ollama доступен" || echo "  ✗ External Ollama недоступен ($EXT) — дашборды используют nginx /ollama/ прокси"

# ── 4. Setup AI Recruiter as systemd service ──────────────────────────
RECRUITER_DIR="/var/www/okk/recruiter"

if [ -f "$RECRUITER_DIR/app.py" ]; then
  # Install Python deps
  if [ -f "$RECRUITER_DIR/requirements.txt" ]; then
    pip3 install -q -r "$RECRUITER_DIR/requirements.txt" 2>/dev/null || true
  fi

  # Warn if service_account.json is missing
  if [ ! -f "$RECRUITER_DIR/service_account.json" ]; then
    echo "WARNING: $RECRUITER_DIR/service_account.json not found!"
    echo "  Upload it manually: scp service_account.json root@<VPS>:$RECRUITER_DIR/"
  fi

  # Write systemd service
  # EnvironmentFile loads GOOGLE_SERVICE_ACCOUNT_INFO if .env exists
  ENV_LINE=""
  if [ -f "$RECRUITER_DIR/.env" ]; then
    ENV_LINE="EnvironmentFile=$RECRUITER_DIR/.env"
  fi

  cat > /etc/systemd/system/recruiter.service << SVC_EOF
[Unit]
Description=STH AI Recruiter (Streamlit)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$RECRUITER_DIR
ExecStart=/usr/bin/python3 -m streamlit run app.py --server.port 8501 --server.address 127.0.0.1 --server.baseUrlPath /recruiter
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
$ENV_LINE

[Install]
WantedBy=multi-user.target
SVC_EOF

  systemctl daemon-reload
  systemctl enable recruiter
  systemctl restart recruiter
  echo "Recruiter service started"
else
  echo "Recruiter app.py not found, skipping service setup"
fi

# ── 6. Setup Задачник API as systemd service ──────────────────────────
TASKS_DIR="/var/www/okk/tasks/api"

if [ -f "$TASKS_DIR/main.py" ]; then
  echo "=== [tasks-api] Setting up FastAPI backend ==="

  # Ensure pip3 is installed
  if ! command -v pip3 &> /dev/null; then
    echo "[tasks-api] pip3 not found, installing..."
    apt-get update -qq && apt-get install -y -qq python3-pip python3-venv
  fi

  # Use a virtualenv to avoid system package conflicts (PEP 668)
  VENV="$TASKS_DIR/.venv"
  if [ ! -d "$VENV" ]; then
    echo "[tasks-api] Creating virtualenv at $VENV"
    python3 -m venv "$VENV" || { apt-get install -y -qq python3-venv && python3 -m venv "$VENV"; }
  fi

  echo "[tasks-api] Installing requirements..."
  "$VENV/bin/pip" install --upgrade pip --quiet
  "$VENV/bin/pip" install -r "$TASKS_DIR/requirements.txt" || {
    echo "[tasks-api] ERROR: pip install failed"
    "$VENV/bin/pip" install -r "$TASKS_DIR/requirements.txt"
  }

  echo "[tasks-api] Installed packages:"
  "$VENV/bin/pip" list 2>/dev/null | grep -iE "fastapi|uvicorn|pydantic" || echo "  (none of fastapi/uvicorn/pydantic found!)"

  # Если /etc/sth-portal.env отсутствует — создаём с дефолтными паролями из DOCS.md.
  # Файл не в git, после первой инициализации пароли можно поменять руками.
  if [ ! -f /etc/sth-portal.env ]; then
    echo "[tasks-api] /etc/sth-portal.env not found — creating with defaults"
    cat > /etc/sth-portal.env << 'ENV_EOF'
ADMIN_PASSWORD=028511
PORTAL_PASSWORD=511028
ENV_EOF
    chmod 600 /etc/sth-portal.env
  fi

  cat > /etc/systemd/system/tasks-api.service << TASKS_EOF
[Unit]
Description=Задачник API (FastAPI)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$TASKS_DIR
ExecStart=$VENV/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8601
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=TASKS_DB=$TASKS_DIR/tasks.db
EnvironmentFile=-/etc/sth-portal.env

[Install]
WantedBy=multi-user.target
TASKS_EOF

  systemctl daemon-reload
  systemctl enable tasks-api
  systemctl restart tasks-api
  sleep 3

  echo "[tasks-api] Service status:"
  systemctl status tasks-api --no-pager -l | head -20 || true
  echo "[tasks-api] Recent logs:"
  journalctl -u tasks-api -n 30 --no-pager || true
  echo "[tasks-api] Health check:"
  curl -sf http://127.0.0.1:8601/health && echo " ✓ API is alive" || echo " ✗ API not responding"
else
  echo "Tasks API main.py not found, skipping"
fi

# ── 7. Test and reload nginx ───────────────────────────────────────────
nginx -t && systemctl reload nginx && echo "nginx reloaded OK"

echo "=== All location blocks in active nginx config ==="
nginx -T 2>/dev/null | grep -nE "^\s*(server|listen|location|include)" | head -60 || true

echo "=== Final health check via nginx (real Host header) ==="
HC=$(curl -s -o /dev/null -w "%{http_code}" -H "Host: 195.208.119.67" http://127.0.0.1/tasks/api/health)
echo "  /tasks/api/health → HTTP $HC"
[ "$HC" = "200" ] && echo " ✓ /tasks/api/ works on real host" || echo " ✗ /tasks/api/ fails on real host"
HO=$(curl -s -o /dev/null -w "%{http_code}" -H "Host: 195.208.119.67" http://127.0.0.1/ollama/api/tags)
echo "  /ollama/api/tags → HTTP $HO"
HR=$(curl -s -o /dev/null -w "%{http_code}" -H "Host: 195.208.119.67" http://127.0.0.1/recruiter/)
echo "  /recruiter/ → HTTP $HR"

echo "=== Setup complete ==="
