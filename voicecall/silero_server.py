"""Silero TTS HTTP-сервер для voicecall.

Запускается на твоём ПК на 127.0.0.1:5001 (по аналогии с Ollama).
Использует Silero v4 (русский), модель ~50 МБ, скачивается при первом старте.

Голоса: aidar, baya, kseniya, xenia, eugene.

Эндпоинт:
  GET /tts?text=...&voice=kseniya&sample_rate=24000
       → wav-байты (audio/wav)
  GET /voices
       → список доступных голосов
  GET /health
       → {ok: true, model_loaded: bool}

Запускается в venv Python 3.12 (PyTorch не имеет wheels под 3.14):
  C:\\ProgramData\\sth\\silero-venv\\Scripts\\python.exe voicecall\\silero_server.py

Если порт 5001 занят — передай SILERO_PORT=NNNN в env.
"""
import io
import os
import sys
import threading
import time
import wave
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import torch
import numpy as np
from flask import Flask, request, send_file, jsonify

MODEL_DIR = Path(os.getenv("SILERO_MODEL_DIR", r"C:\ProgramData\sth\silero-model"))
MODEL_DIR.mkdir(parents=True, exist_ok=True)
MODEL_FILE = MODEL_DIR / "v4_ru.pt"
MODEL_URL = "https://models.silero.ai/models/tts/ru/v4_ru.pt"

DEFAULT_VOICE = "kseniya"  # женский, наш аналог «Алины»
ALL_VOICES = ["aidar", "baya", "kseniya", "xenia", "eugene", "random"]

# Sample rate: 8000 (тел), 24000 (HQ), 48000 (макс). Для нас 24000 — хороший
# баланс качества и размера, потом будем downsample до 8000 для SIP.
DEFAULT_SR = 24000


_model = None
_model_lock = threading.Lock()
_load_error = None


def _ensure_model_file():
    if MODEL_FILE.exists() and MODEL_FILE.stat().st_size > 1_000_000:
        return True
    print(f"[silero] downloading model {MODEL_URL} (~50 MB)...", flush=True)
    try:
        import urllib.request as _ur
        _ur.urlretrieve(MODEL_URL, str(MODEL_FILE))
        print(f"[silero] downloaded {MODEL_FILE.stat().st_size} bytes", flush=True)
        return True
    except Exception as e:
        print(f"[silero] download failed: {e}", flush=True)
        return False


def _load_model():
    global _model, _load_error
    with _model_lock:
        if _model is not None:
            return _model
        if not _ensure_model_file():
            _load_error = "model file missing"
            return None
        try:
            t0 = time.time()
            print(f"[silero] loading model from {MODEL_FILE}...", flush=True)
            device = torch.device("cpu")
            model = torch.package.PackageImporter(str(MODEL_FILE)).load_pickle("tts_models", "model")
            model.to(device)
            _model = model
            print(f"[silero] model loaded in {time.time()-t0:.1f}s", flush=True)
        except Exception as e:
            _load_error = f"{type(e).__name__}: {e}"
            print(f"[silero] load failed: {_load_error}", flush=True)
    return _model


def synthesize_wav(text: str, voice: str = DEFAULT_VOICE,
                   sample_rate: int = DEFAULT_SR) -> bytes:
    if voice not in ALL_VOICES:
        voice = DEFAULT_VOICE
    if sample_rate not in (8000, 24000, 48000):
        sample_rate = DEFAULT_SR
    model = _load_model()
    if model is None:
        raise RuntimeError("model not loaded: " + (_load_error or "?"))
    # Silero apply_tts возвращает torch.Tensor с float32 в [-1, 1]
    audio = model.apply_tts(text=text, speaker=voice, sample_rate=sample_rate,
                            put_accent=True, put_yo=True)
    # Конвертим float32 → int16 PCM
    audio_np = (audio.numpy() * 32767).clip(-32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_np.tobytes())
    return buf.getvalue()


app = Flask(__name__)


@app.route("/health")
def health():
    return jsonify({
        "ok": _model is not None,
        "model_loaded": _model is not None,
        "load_error": _load_error,
        "model_file_exists": MODEL_FILE.exists(),
    })


@app.route("/voices")
def voices():
    return jsonify({"voices": ALL_VOICES, "default": DEFAULT_VOICE})


@app.route("/tts")
def tts():
    text = (request.args.get("text") or "").strip()
    if not text:
        return "text required", 400
    if len(text) > 5000:
        return "text too long", 413
    voice = request.args.get("voice") or DEFAULT_VOICE
    try:
        sr = int(request.args.get("sample_rate") or DEFAULT_SR)
    except ValueError:
        sr = DEFAULT_SR
    try:
        t0 = time.time()
        wav = synthesize_wav(text, voice, sr)
        elapsed = time.time() - t0
        print(f"[silero] tts: {len(text)}ch voice={voice} sr={sr} → "
              f"{len(wav)} bytes in {elapsed:.2f}s", flush=True)
        return send_file(io.BytesIO(wav), mimetype="audio/wav",
                         download_name="tts.wav")
    except Exception as e:
        print(f"[silero] tts error: {type(e).__name__}: {e}", flush=True)
        return f"error: {type(e).__name__}: {e}", 500


def main():
    port = int(os.getenv("SILERO_PORT", "5001"))
    print(f"[silero] starting on 127.0.0.1:{port}", flush=True)
    # Прогрев в фоне — пока сервер слушает, грузим модель
    threading.Thread(target=_load_model, daemon=True).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
