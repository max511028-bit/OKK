# Cloudflare Tunnel для Ollama (замена ngrok-free)

Цель: стабильный туннель `ai.portalsth.ru` → `http://localhost:11434` на домашнем компе.
Зачем: ngrok-free режет HTTP-ответы на 90 секунд и иногда падает. Cloudflare Tunnel — бесплатно, без лимитов, фиксированный URL.

## Что нужно сделать пользователю (≈15 минут)

### 1. Установить cloudflared на Windows

В PowerShell от админа:
```powershell
winget install --id Cloudflare.cloudflared
```
Или скачать вручную: https://github.com/cloudflare/cloudflared/releases/latest → `cloudflared-windows-amd64.exe` → переименовать в `cloudflared.exe` → положить в `C:\Program Files\cloudflared\`.

Проверка:
```powershell
cloudflared --version
```

### 2. Залогиниться в Cloudflare

```powershell
cloudflared tunnel login
```
Откроется браузер → выбрать домен `portalsth.ru` → «Authorize». Сертификат сохранится в `%USERPROFILE%\.cloudflared\cert.pem`.

### 3. Создать туннель

```powershell
cloudflared tunnel create sth-ai
```
В выводе будет `Tunnel ID` (типа `a1b2c3d4-...`) и путь к credentials JSON. Запомнить ID.

### 4. Создать конфиг

Файл `%USERPROFILE%\.cloudflared\config.yml`:
```yaml
tunnel: sth-ai
credentials-file: C:\Users\user\.cloudflared\<TUNNEL_ID>.json

ingress:
  - hostname: ai.portalsth.ru
    service: http://localhost:11434
    originRequest:
      noTLSVerify: true
      connectTimeout: 30s
      # Длинный таймаут на стримы Ollama (важно — это и было слабым местом ngrok)
      tcpKeepAlive: 30s
  - service: http_status:404
```
Замените `<TUNNEL_ID>` на реальный из шага 3.

### 5. Прописать DNS-запись на ai.portalsth.ru

```powershell
cloudflared tunnel route dns sth-ai ai.portalsth.ru
```
Это создаст CNAME `ai.portalsth.ru` → `<TUNNEL_ID>.cfargotunnel.com` в Cloudflare DNS автоматически.

### 6. Установить как Windows-службу (чтобы автозапуск)

```powershell
cloudflared service install
```
Затем:
```powershell
Start-Service cloudflared
Get-Service cloudflared
```
Должно быть `Running`.

### 7. Проверить из браузера

Открыть https://ai.portalsth.ru/api/tags — должен прийти JSON со списком моделей Ollama.

### 8. Сказать порталу новый URL

```powershell
curl -X POST https://portalsth.ru/tasks/api/ai/url -H "Content-Type: application/json" -d '{"url":"https://ai.portalsth.ru"}'
```
(или через админку, если там есть UI ввода).

### 9. Удалить ngrok-автозапуск

В `scripts/start-ai.ps1` (или Task Scheduler) убрать запуск `ngrok` — теперь это делает `cloudflared` через службу.

---

## Проверка end-to-end

1. На VPS: `curl -s https://portalsth.ru/tasks/api/ai/health` → `{"online": true, "models": N}`.
2. Открыть Финансы/OKK → ИИ-чат → задать вопрос → длинные ответы не режутся.
3. Прогнать smoke: в админке кнопка «Запустить сейчас» → должно быть 6/6 ok.
4. Перезагрузить комп → через 30 сек `Get-Service cloudflared` → `Running` без ручных действий.

## Watchdog

`scripts/ai-watchdog.ps1` каждые 60 сек проверяет `http://localhost:11434/api/tags`. Два промаха подряд → `Restart-Service cloudflared` + `Start-Process ollama serve`. Установка через Task Scheduler (см. инструкцию в самом файле).
