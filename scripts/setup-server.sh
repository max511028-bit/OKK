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

# ── 2. Include snippet in site config if not already ──────────────────
if ! grep -q "portal-proxies" "$SITE_CONF" 2>/dev/null; then
  if grep -q "server {" "$SITE_CONF"; then
    sed -i "/server {/a\\    include snippets/portal-proxies.conf;" "$SITE_CONF"
    echo "Snippet included in $SITE_CONF"
  else
    echo "WARNING: could not find 'server {' in $SITE_CONF"
  fi
else
  echo "Snippet already included in $SITE_CONF"
fi

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
  if [ -f "$TASKS_DIR/requirements.txt" ]; then
    pip3 install -q -r "$TASKS_DIR/requirements.txt" 2>/dev/null || true
  fi

  cat > /etc/systemd/system/tasks-api.service << TASKS_EOF
[Unit]
Description=Задачник API (FastAPI)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$TASKS_DIR
ExecStart=/usr/bin/python3 -m uvicorn main:app --host 127.0.0.1 --port 8601
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
  echo "Задачник API service started on :8601"
else
  echo "Tasks API main.py not found, skipping"
fi

# ── 7. Test and reload nginx ───────────────────────────────────────────
nginx -t && systemctl reload nginx && echo "nginx reloaded OK"

echo "=== Setup complete ==="
