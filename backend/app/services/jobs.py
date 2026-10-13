import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from app.config import JOBS, safe_id, localize_path
from app.services.pipeline import process_full_song_pipeline

worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voice-conversion")
lock = threading.RLock()
logger = logging.getLogger(__name__)


def read_job(song_id: str):
    path = JOBS / f"{safe_id(song_id)}.json"
    if not path.is_file():
        return None
    job = json.loads(path.read_text())
    for key in ("source", "output"):
        if job.get(key):
            job[key] = str(localize_path(job[key]))
    if job.get("proof"):
        for key in ("source", "output", "original_vocals", "converted_vocals"):
            if job["proof"].get(key):
                job["proof"][key] = str(localize_path(job["proof"][key]))
    return job


def save_job(job):
    JOBS.mkdir(parents=True, exist_ok=True)
    path = JOBS / f"{safe_id(job['song_id'])}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(job, indent=2))
    temporary.replace(path)


def update_job(song_id, **fields):
    with lock:
        job = read_job(song_id)
        job.update(fields, updated_at=datetime.now(timezone.utc).isoformat())
        save_job(job)
        return job


def recover_interrupted_jobs():
    for path in JOBS.glob("*.json"):
        job = json.loads(path.read_text())
        if job["status"] in {"queued", "processing"}:
            update_job(job["song_id"], status="failed", stage="Proceso interrumpido",
                       error="El servidor se reinicio durante la conversion. Puedes volver a procesar la cancion.")


def run_job(song_id):
    job = update_job(song_id, status="processing")
    try:
        proof = process_full_song_pipeline(
            job["source"], song_id, job["user_id"],
            progress=lambda value, stage: update_job(song_id, progress=value, stage=stage),
            pitch_shift=job["pitch_shift"], steps=job["steps"],
            reference_kind=job["reference_kind"], output_name=job["output_name"],
            language=job.get("language"),
        )
        update_job(song_id, status="completed", progress=100, stage="Completado",
                   output=proof["output"], proof=proof, error=None)
    except Exception as error:
        logger.exception("Conversion failed: %s", song_id)
        update_job(song_id, status="failed", stage="Error de conversion", error=str(error))


def enqueue(source: Path, user_id="luis_felipe", song_id=None, pitch_shift=0,
            steps=30, reference_kind="talk", output_name=None, language=None):
    safe_id(user_id)
    song_id = safe_id(song_id or uuid.uuid4().hex[:12])
    with lock:
        existing = read_job(song_id)
        if existing and existing["status"] in {"queued", "processing"}:
            return existing
        job = {"song_id": song_id, "source": str(source.resolve()), "name": source.name,
               "user_id": user_id, "status": "queued", "progress": 0,
               "stage": "En cola", "error": None, "pitch_shift": pitch_shift,
               "steps": steps, "reference_kind": reference_kind,
               "language": language,
               "output_name": output_name or f"{source.stem}-{user_id}-{song_id}.mp3",
               "created_at": datetime.now(timezone.utc).isoformat()}
        save_job(job)
        worker.submit(run_job, song_id)
        return job
