import uuid
import tempfile
import zipfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from app.config import AUDIO_EXTENSIONS
from app.services import samples, singing_guides
from app.services.prompts import catalog, get_prompt

router = APIRouter()


@router.get("/prompts")
def prompts():
    return {"prompts": catalog()}


@router.get("/{user_id}")
def status(user_id: str):
    from app.services.personal_model import model_status
    try:
        return {**samples.dataset_status(user_id), "model": model_status(user_id)}
    except ValueError as error:
        raise HTTPException(400, str(error))


@router.post("/{user_id}/samples")
async def capture(user_id: str, audio: UploadFile = File(...), prompt_id: str = Form(...),
                  mode: str = Form(...), guide_id: str | None = Form(None),
                  pitch_shift: int = Form(0, ge=-12, le=12),
                  guide_kind: str = Form("mix"), expected_text: str | None = Form(None, max_length=3000)):
    try:
        root = samples.directory(user_id)
        guide = singing_guides.get_guide(user_id, guide_id) if guide_id else None
        prompt = guide or get_prompt(prompt_id)
        if guide and (mode != "sing" or prompt_id != guide_id):
            raise ValueError("La guia debe corresponder a la muestra cantada.")
        if guide_kind not in {"mix", "vocal"} or (guide and guide_kind == "vocal" and not guide["isolated_available"]):
            raise ValueError("Pista guia no disponible.")
        if guide and expected_text is not None and len(samples.tokens(expected_text)) < 2:
            raise ValueError("Confirma las palabras del fragmento antes de guardar la toma.")
    except ValueError as error:
        raise HTTPException(400, str(error))
    if mode not in {"speak", "sing"}:
        raise HTTPException(400, "Tipo de muestra no valido.")
    from app.services.personal_model import model_status
    if model_status(user_id)["status"] == "training":
        raise HTTPException(409, "Espera a que termine el entrenamiento antes de añadir tomas.")
    suffix = Path(audio.filename or "capture.webm").suffix.lower()
    if suffix not in AUDIO_EXTENSIONS:
        raise HTTPException(400, "Formato no compatible.")
    content = await audio.read(20 * 1024 * 1024 + 1)
    if not content or len(content) > 20 * 1024 * 1024:
        raise HTTPException(400, "Audio vacio o mayor a 20 MB.")
    sample_id = uuid.uuid4().hex[:16]
    root.mkdir(parents=True, exist_ok=True)
    filename = sample_id + suffix
    (root / filename).write_bytes(content)
    item = {"id": sample_id, "prompt_id": prompt_id, "language": prompt["language"],
            "mode": mode, "filename": filename, "status": "queued", "issues": []}
    if guide:
        item["expected_text"] = expected_text if expected_text is not None else guide["text"]
        item["guide"] = {key: guide[key] for key in (
            "id", "song_id", "name", "source_sha256", "start_seconds", "duration", "language")}
        item["guide"].update(pitch_shift=pitch_shift, kind=guide_kind,
                             text_source="edited" if item["expected_text"] != guide["text"] else "asr")
    samples.save_sample(user_id, item)
    samples.validator.submit(samples.validate_sample, user_id, sample_id)
    return item


@router.get("/{user_id}/singing-guides")
def guides(user_id: str):
    try:
        return singing_guides.public_state(user_id)
    except ValueError as error:
        raise HTTPException(400, str(error))


class PrepareGuides(BaseModel):
    song_id: str | None = None
    start_seconds: float = Field(0, ge=0, le=3600)
    duration: float = Field(24, ge=12, le=30)
    exclude_song_ids: list[str] = Field(default_factory=list, max_length=100)
    avoid_song_ids: list[str] = Field(default_factory=list, max_length=100)


@router.post("/{user_id}/singing-guides")
def prepare_guides(user_id: str, options: PrepareGuides):
    try:
        return singing_guides.prepare(user_id, options.song_id, options.start_seconds, options.duration,
                                     options.exclude_song_ids, options.avoid_song_ids)
    except ValueError as error:
        raise HTTPException(409, str(error))


@router.get("/{user_id}/singing-guides/{guide_id}/audio")
def singing_audio(user_id: str, guide_id: str, pitch: int = Query(0, ge=-12, le=12), kind: str = "mix"):
    try:
        audio = singing_guides.guide_audio(user_id, guide_id, pitch, kind)
    except ValueError as error:
        raise HTTPException(404, str(error))
    return Response(audio, media_type="audio/flac") if isinstance(audio, bytes) else FileResponse(audio, media_type="audio/flac")


@router.get("/{user_id}/samples/{sample_id}/audio")
def sample_audio(user_id: str, sample_id: str):
    try:
        item = samples.read_sample(user_id, sample_id)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "Muestra no encontrada.")
    return FileResponse(samples.directory(user_id) / item["filename"])


class Review(BaseModel):
    accept: bool
    manual: bool = False


@router.post("/{user_id}/samples/{sample_id}/retry")
def retry(user_id: str, sample_id: str):
    try:
        item = samples.read_sample(user_id, sample_id)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "Muestra no encontrada.")
    if item["status"] != "failed":
        raise HTTPException(409, "Solo se reintentan analisis fallidos.")
    item.update(status="queued", error=None)
    samples.save_sample(user_id, item)
    samples.validator.submit(samples.validate_sample, user_id, sample_id)
    return item


@router.post("/{user_id}/samples/{sample_id}/review")
def review(user_id: str, sample_id: str, decision: Review):
    try:
        item = samples.read_sample(user_id, sample_id)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "Muestra no encontrada.")
    if item["status"] not in {"review", "accepted", "excluded"}:
        raise HTTPException(409, "La toma aun no esta validada o tiene un fallo de audio.")
    if decision.accept and item["alignment"]["coverage"] < 0.6 and not decision.manual:
        raise HTTPException(409, "La coincidencia es baja. Escucha la toma y confirma la revision manual.")
    from app.services.personal_model import model_lock, model_status
    with model_lock:
        if model_status(user_id)["status"] == "training":
            raise HTTPException(409, "Espera a que termine el entrenamiento antes de cambiar las tomas aceptadas.")
        item.update(status="accepted" if decision.accept else "excluded", manual_review=decision.manual)
        samples.save_sample(user_id, item)
    return item


class Training(BaseModel):
    epochs: int | None = Field(None, ge=10, le=500)
    mode: Literal["initial", "fine_tune"] | None = None


@router.post("/{user_id}/train")
def train(user_id: str, options: Training):
    from app.services.personal_model import start_training
    try:
        return start_training(user_id, options.epochs, options.mode)
    except ValueError as error:
        raise HTTPException(409, str(error))


@router.post("/{user_id}/train/restore")
def restore_training(user_id: str):
    from app.services.personal_model import restore_previous_model
    try:
        return restore_previous_model(user_id)
    except ValueError as error:
        raise HTTPException(409, str(error))


@router.post("/{user_id}/train/cancel")
def cancel(user_id: str):
    from app.services.personal_model import cancel_training
    try:
        return cancel_training(user_id)
    except ValueError as error:
        raise HTTPException(409, str(error))


@router.post("/{user_id}/approve")
def approve(user_id: str):
    from app.services.personal_model import approve_model
    try:
        return approve_model(user_id)
    except ValueError as error:
        raise HTTPException(409, str(error))


class Preview(BaseModel):
    song_id: str
    start_seconds: float = Field(30, ge=0)
    language: str
    pitch_shift: int = Field(0, ge=-12, le=12)


@router.post("/{user_id}/preview")
def preview(user_id: str, options: Preview):
    from app.api.songs import local_songs
    from app.services.previews import start_preview
    source = local_songs().get(options.song_id)
    if source is None:
        from app.services.jobs import read_job
        try:
            job = read_job(options.song_id)
            source = Path(job["source"]) if job else None
        except ValueError as error:
            raise HTTPException(400, str(error))
    if not source or options.language not in {"es", "en"}:
        raise HTTPException(400, "Selecciona una cancion y su idioma.")
    try:
        return start_preview(user_id, source, options.start_seconds, options.language, options.pitch_shift)
    except ValueError as error:
        raise HTTPException(409, str(error))


@router.get("/{user_id}/preview/{version}")
def preview_audio(user_id: str, version: str):
    from app.services.personal_model import model_status
    state = model_status(user_id)
    preview = state.get("preview", {})
    key = {"audio": "output", "original": "original", "previous": "previous_output"}.get(version)
    if not key or preview.get("status") != "completed" or not preview.get(key):
        raise HTTPException(404, "Prueba no disponible.")
    return FileResponse(preview[key])


class BundleExport(BaseModel):
    kind: Literal["model", "training"] = "model"


@router.post("/{user_id}/bundles/export")
def export_voice_bundle(user_id: str, options: BundleExport):
    from app.services import model_bundles
    try:
        path = model_bundles.export_bundle(user_id, options.kind)
        return {"download_url": f"/api/studio/{user_id}/bundles/download/{path.stem}",
                "filename": f"voice-{options.kind}.zip"}
    except ValueError as error:
        raise HTTPException(409, str(error))


@router.get("/{user_id}/bundles/download/{bundle_id}")
def download_voice_bundle(user_id: str, bundle_id: str):
    from app.config import safe_id
    from app.services import personal_model
    try:
        safe_id(user_id)
        path = personal_model.PROFILES.parent / "exports" / f"{safe_id(bundle_id)}.zip"
    except ValueError as error:
        raise HTTPException(400, str(error))
    if not path.is_file():
        raise HTTPException(404, "Paquete no disponible; vuelve a exportarlo.")
    return FileResponse(path, media_type="application/zip", filename="voice-bundle.zip",
                        background=BackgroundTask(path.unlink, missing_ok=True))


@router.post("/{user_id}/bundles/import")
async def import_voice_bundle(user_id: str, bundle: UploadFile = File(...)):
    from starlette.concurrency import run_in_threadpool
    from app.services import model_bundles, personal_model
    root = personal_model.PROFILES.parent / "imports"
    root.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="model-", dir=root) as temporary:
            source = Path(temporary) / "model.zip"
            size = 0
            with source.open("wb") as stream:
                while chunk := await bundle.read(1024 * 1024):
                    size += len(chunk)
                    if size > model_bundles.MODEL_LIMIT:
                        raise HTTPException(413, "El ZIP del modelo supera 512 MB.")
                    stream.write(chunk)
            try:
                return await run_in_threadpool(model_bundles.import_model, user_id, source)
            except (ValueError, KeyError, TypeError, RuntimeError, OSError, zipfile.BadZipFile) as error:
                raise HTTPException(400, f"Modelo no importado: {error}")
    finally:
        await bundle.close()
