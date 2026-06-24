# Этот файл импортируется из main.py — добавляет валидаторные эндпоинты.
# Вынесено отдельно, чтобы не разбухал main.py.
import json as _json
import os as _os
import subprocess as _sp
import tempfile as _tmp
import wave as _wave
import datetime as _dt
from typing import Optional

import httpx
from fastapi import HTTPException, Request, UploadFile, File
from pydantic import BaseModel


_vosk_model = None
_vosk_load_error = None


def _get_vosk_model():
    global _vosk_model, _vosk_load_error
    if _vosk_model is not None:
        return _vosk_model
    try:
        from vosk import Model as _VoskModel  # type: ignore
        model_path = _os.getenv("VOSK_MODEL_PATH", "/var/www/okk/tasks/api/vosk-model")
        if not _os.path.isdir(model_path):
            _vosk_load_error = "Vosk model not found at " + model_path
            return None
        _vosk_model = _VoskModel(model_path)
        _vosk_load_error = None
        return _vosk_model
    except Exception as e:
        _vosk_load_error = type(e).__name__ + ": " + str(e)
        return None


class ValidatorResult(BaseModel):
    project_id: str = "tander-sterlitamak-pack"
    project_name: str = "Тандер · Стерлитамак · комплектовщик"
    started_at: str = ""
    ended_at: str = ""
    verdict: str
    stop_reason: Optional[str] = None
    answers: dict = {}
    transcript: list = []
    summary: Optional[str] = None
    browser: Optional[str] = None


class ValidatorClassifyReq(BaseModel):
    text: str
    labels: list[str]
    question: str = ""


class ValidatorSummaryReq(BaseModel):
    transcript: list
    verdict: str
    stop_reason: Optional[str] = None
    project_name: str = ""


def register(app, db, ai_get_url):
    """Регистрирует валидаторные эндпоинты на app. Принимает зависимости:
       app, db (контекст-менеджер), ai_get_url (функция возвращающая URL Ollama).
    """

    @app.post("/validator/result")
    def validator_save_result(payload: ValidatorResult, request: Request):
        now = _dt.datetime.now().isoformat(timespec="seconds")
        started = payload.started_at or now
        ended = payload.ended_at or now
        ip = (request.client.host if request.client else "") or ""
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO candidate_validations "
                "(project_id, project_name, started_at, ended_at, verdict, stop_reason, "
                "answers_json, transcript_json, summary, browser, ip) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    payload.project_id,
                    payload.project_name,
                    started, ended,
                    payload.verdict,
                    payload.stop_reason,
                    _json.dumps(payload.answers, ensure_ascii=False),
                    _json.dumps(payload.transcript, ensure_ascii=False),
                    payload.summary,
                    payload.browser,
                    ip,
                ),
            )
            return {"ok": True, "id": cur.lastrowid}

    @app.get("/validator/results")
    def validator_list_results(limit: int = 50, offset: int = 0):
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        with db() as conn:
            rows = conn.execute(
                "SELECT id, project_id, project_name, started_at, ended_at, "
                "verdict, stop_reason, answers_json, transcript_json, summary "
                "FROM candidate_validations ORDER BY started_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM candidate_validations").fetchone()[0]
        out = []
        for r in rows:
            try:
                answers = _json.loads(r["answers_json"])
            except Exception:
                answers = {}
            try:
                transcript = _json.loads(r["transcript_json"])
            except Exception:
                transcript = []
            out.append({
                "id": r["id"],
                "project_id": r["project_id"],
                "project_name": r["project_name"],
                "started_at": r["started_at"],
                "ended_at": r["ended_at"],
                "verdict": r["verdict"],
                "stop_reason": r["stop_reason"],
                "answers": answers,
                "transcript": transcript,
                "summary": r["summary"],
            })
        return {"items": out, "total": int(total)}

    @app.post("/validator/transcribe")
    async def validator_transcribe(file: UploadFile = File(...)):
        body = await file.read()
        if not body:
            raise HTTPException(400, "Empty audio")
        if len(body) > 5 * 1024 * 1024:
            raise HTTPException(413, "Audio too large (max 5 MB)")
        model = _get_vosk_model()
        if model is None:
            raise HTTPException(503, "Vosk unavailable: " + (_vosk_load_error or "not initialized"))
        with _tmp.NamedTemporaryFile(suffix=".bin", delete=False) as f_in:
            f_in.write(body)
            in_path = f_in.name
        out_path = in_path + ".wav"
        try:
            proc = _sp.run(
                ["ffmpeg", "-y", "-i", in_path, "-ar", "16000", "-ac", "1",
                 "-acodec", "pcm_s16le", out_path],
                capture_output=True, timeout=20,
            )
            if proc.returncode != 0:
                err = proc.stderr.decode("utf-8", "replace")[:300]
                raise HTTPException(500, "ffmpeg failed: " + err)
            from vosk import KaldiRecognizer  # type: ignore
            with _wave.open(out_path, "rb") as wf:
                if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != 16000:
                    raise HTTPException(500, "Bad audio format after conversion")
                rec = KaldiRecognizer(model, wf.getframerate())
                rec.SetWords(False)
                while True:
                    data = wf.readframes(4000)
                    if len(data) == 0:
                        break
                    rec.AcceptWaveform(data)
                final = _json.loads(rec.FinalResult())
            text = (final.get("text") or "").strip()
            return {"text": text}
        finally:
            for p in (in_path, out_path):
                try:
                    _os.unlink(p)
                except Exception:
                    pass

    @app.post("/validator/llm-classify")
    async def validator_llm_classify(req: ValidatorClassifyReq):
        if not req.text or not req.labels:
            raise HTTPException(400, "text and labels required")
        allowed = [str(l).strip() for l in req.labels if l]
        if not allowed:
            raise HTTPException(400, "labels list empty")
        prompt = (
            "Кандидат на собеседовании. Бот спросил: «" + req.question + "»\n"
            "Кандидат ответил: «" + req.text + "»\n\n"
            "Классифицируй ответ строго в одну из меток: " + ", ".join(allowed) + ".\n"
            "ВАЖНО: ответь ОДНИМ СЛОВОМ из списка, без пояснений, без точки, без кавычек."
        )
        body = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.1, "num_predict": 16},
        }
        url = ai_get_url() + "/api/chat"
        try:
            async with httpx.AsyncClient(timeout=10) as cli:
                r = await cli.post(url, json=body)
                if r.status_code != 200:
                    raise HTTPException(503, "LLM HTTP " + str(r.status_code))
                data = r.json()
            raw = (data.get("message") or {}).get("content", "").strip().lower()
            chosen = None
            for lab in allowed:
                if lab.lower() in raw:
                    chosen = lab
                    break
            if chosen is None:
                chosen = "unclear" if "unclear" in [l.lower() for l in allowed] else allowed[-1]
            return {"label": chosen, "raw": raw}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(503, "LLM error: " + type(e).__name__ + ": " + str(e))

    @app.post("/validator/llm-summary")
    async def validator_llm_summary(req: ValidatorSummaryReq):
        lines = []
        for m in (req.transcript or []):
            who = "Бот" if m.get("who") == "bot" else "Кандидат"
            txt = str(m.get("text", "")).strip()
            if txt:
                lines.append(who + ": " + txt)
        if not lines:
            return {"summary": ""}
        dialog = "\n".join(lines[-30:])
        verdict_human = {"passed": "годен", "stopped": "стоп-фактор", "declined": "отказался"}.get(
            req.verdict, req.verdict
        )
        suffix = (" Причина: " + req.stop_reason) if req.stop_reason else ""
        prompt = (
            "Ниже расшифровка короткого скрининг-звонка кандидату на позицию «" + req.project_name + "». "
            "Итог: " + verdict_human + "." + suffix + "\n\n"
            + dialog + "\n\n"
            "Напиши 2-3 короткие фразы РЕКРУТЕРУ: что заметил по кандидату, на что обратить внимание. "
            "Без формальностей, по-деловому, без эмодзи. Не повторяй сухие факты из ответов — "
            "добавь наблюдение."
        )
        body = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.5, "num_predict": 200},
        }
        url = ai_get_url() + "/api/chat"
        try:
            async with httpx.AsyncClient(timeout=20) as cli:
                r = await cli.post(url, json=body)
                if r.status_code != 200:
                    raise HTTPException(503, "LLM HTTP " + str(r.status_code))
                data = r.json()
            text = (data.get("message") or {}).get("content", "").strip()
            return {"summary": text}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(503, "LLM error: " + type(e).__name__ + ": " + str(e))
