import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import AUDIO_EXTENSIONS, JOBS, ORIGINALS, OUTPUTS, PROFILES, STORAGE, safe_id
from app.services.jobs import enqueue, read_job, save_job, lock
from app.services.personal_model import require_model

router = APIRouter()


def local_songs():
    trashed = archived_songs()
    return {"local-" + hashlib.sha256(path.name.encode()).hexdigest()[:12]: path
            for path in sorted(ORIGINALS.glob("*"))
            if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
            and "local-" + hashlib.sha256(path.name.encode()).hexdigest()[:12] not in trashed}


def archived_songs():
    path = STORAGE / "song_trash.json"
    return json.loads(path.read_text()) if path.is_file() else {}


def save_archived_songs(items):
    path = STORAGE / "song_trash.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(items, ensure_ascii=False, indent=2))
    temporary.replace(path)


def get_job(song_id):
    try:
        job = read_job(song_id)
    except ValueError as error:
        raise HTTPException(400, str(error))
    if not job:
        raise HTTPException(404, "Trabajo no encontrado.")
    return job


def public_job(job):
    result = {key: job.get(key) for key in
              ("song_id", "name", "status", "progress", "stage", "error", "proof", "language")}
    if job["status"] == "completed" and not job.get("proof", {}).get("conversion", {}).get("personal_model"):
        result.update(status="legacy", stage="Prueba anterior descartada")
    if job["status"] == "completed":
        result["download_url"] = f"/api/songs/{job['song_id']}/download"
    return result


class ConvertOptions(BaseModel):
    user_id: str = "luis_felipe"
    pitch_shift: int = Field(0, ge=-12, le=12)
    steps: int = Field(30, ge=10, le=50)
    reference_kind: str = "talk"
    language: str | None = None


def validate_profile(options):
    try:
        safe_id(options.user_id)
    except ValueError as error:
        raise HTTPException(400, str(error))
    try:
        require_model(options.user_id)
    except ValueError as error:
        raise HTTPException(409, str(error))


def song_languages():
    path = STORAGE / "song_languages.json"
    return json.loads(path.read_text()) if path.is_file() else {}


class Language(BaseModel):
    language: str


@router.patch("/{song_id}/language")
def set_language(song_id: str, options: Language):
    if options.language not in {"es", "en"}:
        raise HTTPException(400, "Cancion o idioma no valido.")
    with lock:
        if song_id not in local_songs():
            job = get_job(song_id)
            if job["status"] in {"queued", "processing"}:
                raise HTTPException(409, "La cancion esta en proceso.")
            job["language"] = options.language
            save_job(job)
            return {"language": options.language}
        languages = song_languages()
        languages[song_id] = options.language
        path = STORAGE / "song_languages.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(languages))
        temporary.replace(path)
    return {"language": options.language}


@router.get("/library")
def library():
    entries = []
    languages = song_languages()
    for song_id, path in local_songs().items():
        job = read_job(song_id)
        entries.append({"song_id": song_id, "name": path.name,
                        "original_url": f"/api/songs/{song_id}/original",
                        **(public_job(job) if job else {"status": "pending", "progress": 0})})
        entries[-1]["language"] = languages.get(song_id)
    trashed_ids = archived_songs()
    uploads = [public_job(json.loads(path.read_text())) for path in JOBS.glob("*.json")
               if not path.stem.startswith("local-") and path.stem not in trashed_ids]
    trash = []
    for song_id, archived in trashed_ids.items():
        job = read_job(song_id)
        trash.append({"song_id": song_id, "name": archived.get("name") or
                      (job or {}).get("name", song_id), "status": "archived"})
    return {"songs": entries, "uploads": uploads, "trash": trash}


@router.delete("/{song_id}")
def archive_song(song_id: str):
    try:
        safe_id(song_id)
    except ValueError as error:
        raise HTTPException(400, str(error))
    with lock:
        path = local_songs().get(song_id)
        job = read_job(song_id)
        if path is None and job is None:
            raise HTTPException(404, "Cancion no encontrada.")
        if job and job["status"] in {"queued", "processing"}:
            raise HTTPException(409, "Espera a que termine la conversion antes de borrar la cancion.")
        trash = archived_songs()
        if song_id in trash:
            raise HTTPException(409, "La cancion ya esta en la papelera.")
        name = path.name if path else job.get("name", song_id)
        trash[song_id] = {"name": name, "archived_at": datetime.now(timezone.utc).isoformat()}
        save_archived_songs(trash)
    return {"song_id": song_id, "name": name, "status": "archived"}


@router.post("/{song_id}/restore")
def restore_song(song_id: str):
    try:
        safe_id(song_id)
    except ValueError as error:
        raise HTTPException(400, str(error))
    with lock:
        trash = archived_songs()
        if song_id not in trash:
            raise HTTPException(404, "Cancion no encontrada en la papelera.")
        archived = trash.pop(song_id)
        save_archived_songs(trash)
    return {"song_id": song_id, "name": archived.get("name", song_id), "status": "restored"}


@router.post("/library/convert")
def convert_library(options: ConvertOptions):
    validate_profile(options)
    jobs = []
    languages = song_languages()
    if any(song_id not in languages for song_id in local_songs()):
        raise HTTPException(400, "Selecciona español o inglés para cada cancion antes de convertir el lote.")
    for song_id, path in local_songs().items():
        settings = {**options.model_dump(), "language": languages[song_id]}
        existing = read_job(song_id)
        if (existing and existing["status"] == "completed"
                and Path(existing.get("output", "")).is_file()
                and existing.get("proof", {}).get("conversion", {}).get("personal_model")
                and existing.get("proof", {}).get("model_version") == require_model(options.user_id)["version"]
                and all(existing.get(key) == value for key, value in settings.items())):
            jobs.append(public_job(existing))
            continue
        jobs.append(public_job(enqueue(path, song_id=song_id, output_name=f"{path.stem}.mp3",
                                       **settings)))
    return {"jobs": jobs}


@router.post("/{song_id}/convert")
def convert_local(song_id: str, options: ConvertOptions):
    validate_profile(options)
    source = local_songs().get(song_id)
    if not source:
        existing = read_job(song_id)
        source = Path(existing["source"]) if existing else None
    if not source or not source.is_file():
        raise HTTPException(404, "Cancion no encontrada.")
    language = song_languages().get(song_id) or options.language
    if language not in {"es", "en"}:
        raise HTTPException(400, "Selecciona el idioma de la cancion.")
    settings = {**options.model_dump(), "language": language}
    return public_job(enqueue(source, song_id=song_id, output_name=f"{source.stem}.mp3",
                              **settings))


@router.post("")
@router.post("/", include_in_schema=False)
async def upload(song: UploadFile = File(...), user_id: str = Form("luis_felipe"),
                  pitch_shift: int = Form(0), steps: int = Form(30),
                  reference_kind: str = Form("talk"), language: str = Form(...)):
    options = ConvertOptions(user_id=user_id, pitch_shift=pitch_shift, steps=steps,
                             reference_kind=reference_kind, language=language)
    if language not in {"es", "en"}:
        raise HTTPException(400, "Idioma no valido.")
    try:
        safe_id(user_id)
    except ValueError as error:
        raise HTTPException(400, str(error))
    filename = Path(song.filename or "song.mp3").name
    if Path(filename).suffix.lower() not in AUDIO_EXTENSIONS:
        raise HTTPException(400, "Formato de audio no compatible.")
    content = await song.read(100 * 1024 * 1024 + 1)
    if not content or len(content) > 100 * 1024 * 1024:
        raise HTTPException(400, "Cancion vacia o mayor a 100 MB.")
    song_id = uuid.uuid4().hex[:12]
    directory = STORAGE / "uploads" / song_id
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / filename
    source.write_bytes(content)
    job = {"song_id": song_id, "name": filename, "source": str(source),
           "status": "pending", "progress": 0, "stage": "Pendiente", "error": None,
           "output_name": f"{source.stem}-{song_id}.mp3", **options.model_dump()}
    save_job(job)
    return {**public_job(job), "original_url": f"/api/songs/{song_id}/original"}


@router.get("/{song_id}/status")
def status(song_id: str):
    return public_job(get_job(song_id))


@router.get("/{song_id}/original")
def original(song_id: str):
    path = local_songs().get(song_id)
    if path is None:
        path = Path(get_job(song_id)["source"])
    if not path.is_file():
        raise HTTPException(404, "Audio no encontrado.")
    return FileResponse(path)


@router.get("/{song_id}/waveform")
def waveform(song_id: str):
    import numpy as np
    from app.services.audio import decode

    path = local_songs().get(song_id)
    if path is None:
        path = Path(get_job(song_id)["source"])
    cache = STORAGE / "waveforms" / f"{safe_id(song_id)}.json"
    if cache.is_file() and cache.stat().st_mtime >= path.stat().st_mtime:
        return json.loads(cache.read_text())
    audio = decode(path, 8000)
    peaks = np.array([np.sqrt(np.mean(part ** 2)) for part in np.array_split(audio, 160)])
    peaks /= max(float(peaks.max()), 1e-8)
    data = {"peaks": peaks.round(3).tolist(), "duration": len(audio) / 8000}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data))
    return data


@router.get("/{song_id}/download")
def download(song_id: str):
    job = get_job(song_id)
    if job["status"] != "completed":
        raise HTTPException(409, "La conversion no esta completada.")
    path = Path(job["output"])
    if not path.is_file():
        raise HTTPException(404, "Resultado no encontrado.")
    return FileResponse(path, media_type="audio/mpeg", filename=path.name)


@router.get("/{song_id}/vocals/{version}")
def vocals(song_id: str, version: str):
    job = get_job(song_id)
    key = {"original": "original_vocals", "converted": "converted_vocals"}.get(version)
    if not key or job["status"] != "completed":
        raise HTTPException(404, "Pista vocal no disponible.")
    return FileResponse(job["proof"][key], media_type="audio/flac")
