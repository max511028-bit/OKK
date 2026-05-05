#!/bin/bash
# Server setup script: nginx proxy for Ollama + AI Recruiter service
# Runs on VPS after rsync deploy

set -e

SITE_CONF=""
for f in /etc/nginx/sites-enabled/*; do
  [ -f "$f" ] && SITE_CONF="$f" && break
done
if [ -z "$SITE_CONF" ]; then
  SITE_CONF="/etc/nginx/nginx.conf"
fi

# ── 1. Write Ollama + Recruiter proxy snippet ──────────────────────────
cat > /etc/nginx/snippets/portal-proxies.conf << 'NGINX_EOF'
# Ollama local AI proxy
location /ollama/ {
    proxy_pass http://127.0.0.1:11434/;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_read_timeout 300s;
    proxy_connect_timeout 30s;
    proxy_buffering off;
    add_header Access-Control-Allow-Origin * always;
}

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

# Задачник API (FastAPI on port 8601)
location /tasks/api/ {
    proxy_pass http://127.0.0.1:8601/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 60s;
}
NGINX_EOF

# ── 2. Include snippet in site config (robust, replaces any prior misplaced include) ──
python3 - "$SITE_CONF" << 'PYEOF'
import re, sys
path = sys.argv[1]
with open(path) as f:
    src = f.read()

# 1. Drop ALL existing portal-proxies includes (they may be in the wrong context)
src = re.sub(r'^[ \t]*include\s+snippets/portal-proxies\.conf\s*;[ \t]*\n', '', src, flags=re.MULTILINE)

# 2. Find the active server block — the one containing `listen` (skip commented).
#    Walk braces manually to locate the opening { of that block.
i, n = 0, len(src)
target = None
while i < n:
    m = re.search(r'\bserver\b\s*\{', src[i:])
    if not m: break
    abs_open = i + m.end() - 1   # position of '{'
    # Scan the block contents to see if it has an uncommented `listen`
    depth, j = 1, abs_open + 1
    block_start = j
    while j < n and depth > 0:
        c = src[j]
        if c == '{': depth += 1
        elif c == '}': depth -= 1
        j += 1
    block = src[block_start:j-1]
    # strip comments line-by-line
    uncommented = '\n'.join(re.sub(r'#.*$', '', line) for line in block.splitlines())
    if re.search(r'\blisten\b', uncommented):
        target = abs_open
        break
    i = j

if target is None:
    print("ERROR: no active server { ... listen ... } block found", file=sys.stderr)
    sys.exit(1)

# 3. Insert include right after the opening brace of that server block
new = src[:target+1] + "\n    include snippets/portal-proxies.conf;" + src[target+1:]
with open(path, 'w') as f:
    f.write(new)
print(f"Include inserted into active server block at offset {target} of {path}")
PYEOF

# ── 3. Install Ollama if not present ──────────────────────────────────
if ! command -v ollama &> /dev/null; then
  echo "Installing Ollama..."
  curl -fsSL https://ollama.ai/install.sh | sh
else
  echo "Ollama already installed: $(ollama --version)"
fi

# ── 4. Start Ollama service ────────────────────────────────────────────
systemctl enable ollama 2>/dev/null || true
systemctl start ollama 2>/dev/null || true
sleep 2

# Pull default model if no models installed
if ! ollama list 2>/dev/null | grep -q ":"; then
  echo "Pulling default model llama3.2:3b..."
  ollama pull llama3.2:3b
fi
echo "Models available: $(ollama list 2>/dev/null | grep -v NAME | awk '{print $1}' | tr '\n' ' ')"

# ── 5. Setup AI Recruiter as systemd service ──────────────────────────
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

echo "=== nginx tasks/api proxy config ==="
nginx -T 2>/dev/null | grep -A3 "tasks/api" | head -10 || echo "  (proxy block not found in nginx -T)"

echo "=== Final health check via nginx ==="
curl -sf http://127.0.0.1/tasks/api/health && echo " ✓ /tasks/api/ proxy works" || echo " ✗ /tasks/api/ proxy failed"

echo "=== Setup complete ==="
