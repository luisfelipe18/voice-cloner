import uuid
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.songs import get_job, local_songs
from app.config import AUDIO_EXTENSIONS, safe_id
from app.services import performances as service
from app.services.audio import decode, fingerprint

router = APIRouter()


def source_for(song_id):
    try:
        safe_id(song_id)
    except ValueError as error:
        raise HTTPException(400, str(error))
    source = local_songs().get(song_id)
    if source is None:
        source = Path(get_job(song_id)["source"])
    if not source.is_file():
        raise HTTPException(404, "Cancion no encontrada.")
    return source


@router.get("/{song_id}")
def session(song_id: str):
    source = source_for(song_id)
    item = service.public(service.read(song_id))
    try:
        item["song_duration"] = sf.info(source).duration
    except RuntimeError:
        item["song_duration"] = None
    return item


class Lyrics(BaseModel):
    lyrics: str = Field(min_length=1, max_length=30000)
    language: str


@router.put("/{song_id}/lyrics")
def lyrics(song_id: str, options: Lyrics):
    source_for(song_id)
    if options.language not in {"es", "en"} or not service.tokens(options.lyrics):
        raise HTTPException(400, "Letra o idioma no valido.")
    with service.lock:
        item = service.read(song_id)
        item.update(lyrics=options.lyrics.strip(), language=options.language)
        service.save(item)
        return service.public(item)


async def receive_audio(song_id, upload, stereo=False):
    extension = Path(upload.filename or "").suffix.lower()
    if extension not in AUDIO_EXTENSIONS:
        raise HTTPException(400, "Formato de audio no compatible.")
    content = await upload.read(100 * 1024 * 1024 + 1)
    if not content or len(content) > 100 * 1024 * 1024:
        raise HTTPException(400, "Audio vacio o mayor a 100 MB.")
    root = service.directory(song_id) / "takes"
    root.mkdir(parents=True, exist_ok=True)
    identity = uuid.uuid4().hex[:12]
    temporary = root / f"{identity}.upload{extension}"
    temporary.write_bytes(content)
    try:
        audio = decode(temporary, max_seconds=1200.1)
        duration = len(audio) / 44100
        if not 0.15 <= duration <= 1200:
            raise ValueError("La toma debe durar entre 0.15 segundos y 20 minutos.")
        if np.sqrt(np.mean(audio ** 2)) < 0.001:
            raise ValueError("No se detecto suficiente audio en la toma.")
        path = root / f"{identity}.wav"
        if stereo:
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(temporary), "-t", "1200.1",
                            "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(path)],
                           check=True, capture_output=True)
        else:
            sf.write(path, audio, 44100, subtype="PCM_16")
        return {"id": identity, "filename": f"takes/{path.name}", "duration": round(duration, 3),
                "sha256": fingerprint(path), "status": "queued", "confirmed_words": [],
                "clipped_fraction": round(float(np.mean(np.abs(audio) >= 0.99)), 4)}
    except (ValueError, RuntimeError) as error:
        raise HTTPException(400, str(error))
    except Exception:
        raise HTTPException(400, "No se pudo decodificar la toma de audio.")
    finally:
        temporary.unlink(missing_ok=True)


@router.post("/{song_id}/takes")
async def upload_take(song_id: str, audio: UploadFile = File(...), mode: str = Form(...),
                      song_start: float = Form(0), parent_id: str | None = Form(None),
                      word_index: int | None = Form(None), target_start: float = Form(0),
                      target_end: float = Form(0)):
    source_for(song_id)
    if mode not in {"read", "sing"} or not np.isfinite(song_start) or not 0 <= song_start <= 1200:
        raise HTTPException(400, "Modo o inicio de toma no valido.")
    with service.lock:
        before = service.read(song_id)
        if not before["lyrics"]:
            raise HTTPException(409, "Guarda la letra antes de grabar.")
        parent = service.take_from(before, parent_id) if parent_id else None
        if parent_id:
            if not parent or parent.get("parent_id") or parent["mode"] != mode:
                raise HTTPException(404, "Toma original no encontrada.")
            if parent["lyrics"] != before["lyrics"] or parent["language"] != before["language"]:
                raise HTTPException(409, "La letra cambio. Selecciona una toma de la letra actual.")
            words = parent.get("words", [])
            if word_index is None or not 0 <= word_index < len(words):
                raise HTTPException(400, "Palabra no encontrada.")
            if mode == "sing" and not 0 <= target_start < target_end <= parent["duration"]:
                raise HTTPException(400, "Revisa el intervalo de la palabra cantada.")
    take = await receive_audio(song_id, audio)
    take.update(mode=mode, language=before["language"], lyrics=before["lyrics"],
                expected_text=parent["words"][word_index]["text"] if parent else before["lyrics"],
                song_start=song_start, parent_id=parent_id, word_index=word_index,
                target_start=target_start, target_end=target_end, approved=False)
    with service.lock:
        item = service.read(song_id)
        item["takes"].append(take)
        if not parent_id:
            item["selected"][mode] = take["id"]
        service.save(item)
    service.validator.submit(service.analyze, song_id, take["id"])
    return service.public(item)


class Selection(BaseModel):
    take_id: str


@router.patch("/{song_id}/selection")
def selection(song_id: str, options: Selection):
    source_for(song_id)
    with service.lock:
        item = service.read(song_id)
        take = service.take_from(item, options.take_id)
        if not take or take.get("parent_id"):
            raise HTTPException(404, "Toma no encontrada.")
        item["selected"][take["mode"]] = take["id"]
        service.save(item)
        return service.public(item)


@router.post("/{song_id}/takes/{take_id}/retry")
def retry(song_id: str, take_id: str):
    source_for(song_id)
    with service.lock:
        item = service.read(song_id)
        take = service.take_from(item, take_id)
        if not take:
            raise HTTPException(404, "Toma no encontrada.")
        if take["status"] in {"queued", "analyzing"}:
            raise HTTPException(409, "Reconocimiento en curso.")
        take.update(status="queued", error=None)
        service.save(item)
    service.validator.submit(service.analyze, song_id, take_id)
    return service.public(item)


class WordReview(BaseModel):
    word_index: int = Field(ge=0)
    confirmed: bool


@router.patch("/{song_id}/takes/{take_id}/words")
def review_word(song_id: str, take_id: str, options: WordReview):
    source_for(song_id)
    with service.lock:
        item = service.read(song_id)
        take = service.take_from(item, take_id)
        if not take or options.word_index >= len(take.get("words", [])):
            raise HTTPException(404, "Palabra no encontrada.")
        confirmed = set(take.get("confirmed_words", []))
        if options.confirmed:
            confirmed.add(options.word_index)
        else:
            confirmed.discard(options.word_index)
        take["confirmed_words"] = sorted(confirmed)
        service.save(item)
        return service.public(item)


class CorrectionReview(BaseModel):
    approved: bool
    manual: bool = False


@router.patch("/{song_id}/takes/{take_id}/correction")
def review_correction(song_id: str, take_id: str, options: CorrectionReview):
    source_for(song_id)
    with service.lock:
        item = service.read(song_id)
        take = service.take_from(item, take_id)
        if not take or not take.get("parent_id"):
            raise HTTPException(404, "Repeticion no encontrada.")
        if options.approved and (take["status"] != "review" or
                                any(word["status"] != "matched" for word in take.get("words", []))) and not options.manual:
            raise HTTPException(409, "Escucha y confirma la palabra: el reconocimiento no coincide.")
        if options.approved and take["mode"] == "sing":
            target = take["target_end"] - take["target_start"]
            if not 0.5 <= take["duration"] / target <= 2:
                raise HTTPException(409, "La duracion no coincide con el intervalo cantado. Repite solo esa palabra.")
            for other in item["takes"]:
                if other.get("parent_id") == take["parent_id"] and other["id"] != take_id and other.get("approved"):
                    overlaps = max(other["target_start"], take["target_start"]) < min(other["target_end"], take["target_end"])
                    if overlaps and other["word_index"] != take["word_index"]:
                        raise HTTPException(409, "El intervalo se solapa con otra palabra corregida.")
        if options.approved:
            for other in item["takes"]:
                if other.get("parent_id") == take["parent_id"] and other.get("word_index") == take["word_index"]:
                    other["approved"] = False
        take["approved"] = options.approved
        service.save(item)
        return service.public(item)


@router.get("/{song_id}/takes/{take_id}/audio")
def take_audio(song_id: str, take_id: str):
    source_for(song_id)
    take = service.take_from(service.read(song_id), take_id)
    if not take:
        raise HTTPException(404, "Toma no encontrada.")
    return FileResponse(service.directory(song_id) / take["filename"], media_type="audio/wav")


@router.post("/{song_id}/backing")
def backing(song_id: str):
    source = source_for(song_id)
    with service.lock:
        item = service.read(song_id)
        if item["backing"]["status"] in {"processing", "ready"}:
            return service.public(item)
        # Reuse existing separation without overwriting a user's conversion assets.
        try:
            job = get_job(song_id)
        except HTTPException:
            job = None
        if job and job.get("proof", {}).get("source_sha256") == fingerprint(source):
            from app.config import localize_path
            path = localize_path(job["proof"]["original_vocals"]).parent / "stems.json"
            if path.is_file():
                import json
                stems = json.loads(path.read_text())
                instrumental = localize_path(stems["instrumental"])
                if instrumental.is_file():
                    item["backing"] = {"status": "ready", "path": str(instrumental), "kind": "separated"}
                    service.save(item)
                    return service.public(item)
        item["backing"] = {"status": "processing"}
        service.save(item)
    service.renderer.submit(service.prepare_backing, song_id, source)
    return service.public(item)


@router.post("/{song_id}/backing/upload")
async def upload_backing(song_id: str, audio: UploadFile = File(...)):
    source_for(song_id)
    with service.lock:
        if service.read(song_id)["backing"]["status"] == "processing":
            raise HTTPException(409, "Separacion en curso.")
    take = await receive_audio(song_id, audio, stereo=True)
    with service.lock:
        item = service.read(song_id)
        item["backing"] = {"status": "ready", "kind": "imported",
                           "path": str(service.directory(song_id) / take["filename"])}
        service.save(item)
        return service.public(item)


@router.get("/{song_id}/backing/audio")
def backing_audio(song_id: str):
    source_for(song_id)
    backing = service.read(song_id)["backing"]
    if backing["status"] != "ready":
        raise HTTPException(404, "Instrumental no disponible.")
    return FileResponse(backing["path"])


class Mix(BaseModel):
    take_id: str
    user_id: str = "luis_felipe"
    pitch_shift: int = Field(0, ge=-12, le=12)
    vocal_gain_db: float = Field(0, ge=-18, le=18)
    backing_gain_db: float = Field(0, ge=-18, le=6)
    offset_ms: int = Field(0, ge=-5000, le=5000)
    cleanup: bool = True


@router.post("/{song_id}/mix")
def mix(song_id: str, options: Mix):
    source_for(song_id)
    with service.lock:
        item = service.read(song_id)
        take = service.take_from(item, options.take_id)
        if not take or take.get("parent_id") or take["mode"] != "sing":
            raise HTTPException(400, "Selecciona una toma cantada completa.")
        if take["lyrics"] != item["lyrics"] or take["language"] != item["language"]:
            raise HTTPException(409, "La toma corresponde a otra version de la letra.")
        if take["status"] != "review":
            raise HTTPException(409, "El reconocimiento de la toma aun no esta listo.")
        if any(word["status"] in {"missing", "uncertain"} for word in service.resolved_words(item, take)):
            raise HTTPException(409, "Revisa las palabras pendientes de la toma cantada.")
        if item["backing"]["status"] != "ready":
            raise HTTPException(409, "Falta el instrumental.")
        if any(export["status"] == "processing" for export in item["exports"]):
            raise HTTPException(409, "Mezcla en curso.")
        try:
            model = service.require_model(safe_id(options.user_id))
        except ValueError as error:
            raise HTTPException(409, str(error))
        identity_model = {key: model[key] for key in ("version", "checkpoint_sha256", "dataset_sha256")}
        identity = uuid.uuid4().hex[:12]
        item["exports"].append({"id": identity, "status": "processing", "take_id": take["id"],
                               "progress": 0, "stage": "En cola para aplicar tu modelo personal",
                               "model_version": model["version"],
                               "settings": options.model_dump(exclude={"take_id"})})
        service.save(item)
    service.renderer.submit(service.render_mix, song_id, identity, item, take["id"],
                            options.model_dump(exclude={"take_id"}), identity_model)
    return service.public(item)


@router.get("/{song_id}/exports/{export_id}/audio")
def export_audio(song_id: str, export_id: str):
    source_for(song_id)
    export = next((value for value in service.read(song_id)["exports"] if value["id"] == export_id), None)
    if not export or export["status"] != "completed":
        raise HTTPException(404, "Mezcla no disponible.")
    return FileResponse(export["path"], media_type="audio/mpeg", filename=f"mi-interpretacion-{song_id}.mp3")


@router.get("/{song_id}/exports/{export_id}/vocals")
def export_vocals(song_id: str, export_id: str):
    source_for(song_id)
    export = next((value for value in service.read(song_id)["exports"] if value["id"] == export_id), None)
    if not export or export["status"] != "completed" or not export.get("vocal_path"):
        raise HTTPException(404, "Voz convertida no disponible.")
    return FileResponse(export["vocal_path"], media_type="audio/flac", filename=f"mi-voz-modelo-{song_id}.flac")
