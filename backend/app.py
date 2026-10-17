"""FastAPI app (uvicorn `backend.app:app`). No auth: LiteLLM in front holds the key."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from .batcher import Batcher
from .errors import ApiError
from .request_log import RequestLog
from .settings import Settings
from .speech import MAX_INPUT_CHARS, MAX_SPEED, speak
from .vram import measure_load
from .voices import VoiceBank, load_voice_bank

log = logging.getLogger("backend.app")


@dataclass
class Engines:
    batcher: Batcher
    split_long_run: Callable[[str, int], list[str]]
    transcribe: Callable[..., dict]  # (model, audio, language, temperature, prompt, response_format, with_words)
    model_names: list[str]
    whisper_names: list[str]
    vram_mib: dict[str, dict]


def load_engines(settings: Settings) -> Engines:
    """Real GPU engines; heavy imports stay here so everything else is importable without torch."""
    from . import models_manifest as manifest
    from .f5_engine import F5Engine
    from .whisper_engine import WhisperEngines

    models = manifest.enabled_models(manifest.load_models(settings.models_manifest))
    vram: dict[str, dict] = {}
    f5 = measure_load("f5-tts-th", lambda: F5Engine(manifest.local_path(models["f5-tts-th"]),
                                                    manifest.local_path(models["vocos-mel-24khz"])), vram)  # + vocoder
    whisper = WhisperEngines()
    for name, entry in models.items():
        if name.startswith("whisper-"):
            measure_load(name, lambda n=name, e=entry: whisper.load(n, manifest.local_path(e)), vram)
    return Engines(Batcher(f5.synthesize_batch, settings.batch_max, settings.batch_wait_ms), f5.split_long_run,
                   whisper.transcribe, ["f5-tts-th", *whisper.names], whisper.names, vram)


class SpeechRequest(BaseModel):
    model: str = "f5-tts-th"
    input: str
    voice: str
    response_format: str = "wav"
    speed: float | None = None
    seed: int | None = None


def create_app(settings: Settings | None = None, engine_loader: Callable[[Settings], Engines] = load_engines) -> FastAPI:
    settings = settings or Settings.from_env()
    state: dict[str, Any] = {"engines": None, "error": None}
    voices: VoiceBank = VoiceBank()
    request_log = RequestLog(settings.log_dir)
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="whisper")

    def load_in_background() -> None:  # /ready answers 503 while this runs
        try:
            state["engines"] = engine_loader(settings)
        except Exception as exc:
            state["error"] = str(exc)
            log.exception("model loading failed")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal voices
        voices = load_voice_bank(settings.voices_dir)
        log.info("voices ok=%s refused=%s", sorted(voices.voices), voices.refused)
        threading.Thread(target=load_in_background, name="model-loader", daemon=True).start()
        yield
        if state["engines"]:
            state["engines"].batcher.close()

    app = FastAPI(lifespan=lifespan)

    def engines() -> Engines:
        if state["engines"] is None:
            raise ApiError(503, "models not loaded yet" + (f": {state['error']}" if state["error"] else ""))
        return state["engines"]

    def error(status: int, message: str) -> JSONResponse:
        return JSONResponse({"error": {"message": message, "type": "server_error" if status >= 500 else "invalid_request_error"}},
                            status_code=status)

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error(exc.status, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error(400, "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()))

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return error(500, "internal error")

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        began = time.perf_counter()
        request.state.model = "-"
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            status = 500
            raise
        finally:
            if request.url.path.startswith("/v1/"):
                request_log.write(request.state.model, (time.perf_counter() - began) * 1000, status)
        return response

    @app.get("/ready")
    async def ready() -> JSONResponse:
        if state["engines"] is None:
            return error(503, "models not loaded yet" + (f": {state['error']}" if state["error"] else ""))
        loaded = state["engines"]
        return JSONResponse({"models": loaded.model_names,
                             "vram_mib": {m: v.get("nvidia_smi") for m, v in loaded.vram_mib.items()},
                             "vram_detail_mib": loaded.vram_mib})

    @app.get("/stats")
    async def stats() -> JSONResponse:
        return JSONResponse(engines().batcher.stats.snapshot())

    @app.get("/voices-manifest")
    async def voices_manifest() -> dict:
        return voices.listing()

    @app.post("/v1/audio/speech")
    async def speech(body: SpeechRequest, request: Request) -> Response:
        request.state.model = body.model[:64]
        speed = 1.0 if body.speed is None else body.speed
        if body.response_format != "wav":
            raise ApiError(400, "only response_format 'wav' is supported")
        if not 0 < speed <= MAX_SPEED:
            raise ApiError(400, f"speed must be above 0 and at most {MAX_SPEED}")
        if not body.input.strip() or len(body.input) > MAX_INPUT_CHARS:
            raise ApiError(400, f"input must be 1-{MAX_INPUT_CHARS} characters")
        voice = voices.find(body.voice, settings.default_voice)
        loaded = engines()
        seed = settings.default_seed if body.seed is None else body.seed
        wav = await speak(loaded.batcher, voice, body.input, seed, speed, loaded.split_long_run)
        return Response(wav, media_type="audio/wav")

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(request: Request) -> JSONResponse:
        form = await request.form()  # needs python-multipart
        model = str(form.get("model", ""))
        request.state.model = model[:64]
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            raise ApiError(400, "multipart field 'file' is required")
        response_format = str(form.get("response_format") or "json")
        if response_format not in ("json", "verbose_json"):
            raise ApiError(400, "response_format must be json or verbose_json")
        try:
            temperature = float(form.get("temperature") or 0.0)
        except ValueError:
            raise ApiError(400, "temperature must be a number") from None
        granularities = [*form.getlist("timestamp_granularities[]"), *form.getlist("timestamp_granularities")]
        loaded = engines()
        if model not in loaded.whisper_names:
            raise ApiError(400, f"unknown model {model!r}; available: {loaded.whisper_names}")
        audio = await upload.read()
        if not audio:
            raise ApiError(400, "file is empty")
        result = await asyncio.get_running_loop().run_in_executor(
            pool, loaded.transcribe, model, audio, str(form.get("language") or "") or None, temperature,
            str(form.get("prompt") or "") or None, response_format, "word" in granularities)
        return JSONResponse(result)

    return app


app = create_app()
