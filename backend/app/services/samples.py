import json
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from app.config import PROFILES, safe_id
from app.services.audio import decode, fingerprint
from app.services.prompts import get_prompt

validator = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sample-review")
sample_lock = threading.RLock()
_recognizer = None


def directory(user_id):
    return PROFILES / safe_id(user_id) / "samples"


def save_sample(user_id, item):
    root = directory(user_id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{safe_id(item['id'])}.json"
    with sample_lock:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(item, ensure_ascii=False, indent=2))
        temporary.replace(path)


def read_sample(user_id, sample_id):
    return json.loads((directory(user_id) / f"{safe_id(sample_id)}.json").read_text())


def list_samples(user_id):
    return [json.loads(path.read_text()) for path in sorted(directory(user_id).glob("*.json"))]


def tokens(text):
    clean = unicodedata.normalize("NFKD", text.lower())
    clean = "".join(character for character in clean if not unicodedata.combining(character))
    return re.findall(r"[a-z]+(?:'[a-z]+)?", clean)


def compare_words(expected, chunks):
    wanted = tokens(expected)
    heard = [word for chunk in chunks for word in tokens(chunk["text"])]
    matched, missing = set(), []
    for tag, start, end, *_ in SequenceMatcher(None, wanted, heard, autojunk=False).get_opcodes():
        if tag == "equal":
            matched.update(range(start, end))
        elif tag in {"delete", "replace"}:
            missing.extend(wanted[start:end])
    return {"coverage": round(len(matched) / max(1, len(wanted)), 3),
            "matched_words": len(matched), "expected_words": len(wanted),
            "missing_words": missing, "words": chunks}


def transcribe(audio, language):
    global _recognizer
    if _recognizer is None:
        import torch
        torch.set_num_threads(4)
        from app.config import MODELS
        from huggingface_hub import snapshot_download
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
        cache = str(MODELS / "huggingface/hub")
        path = snapshot_download("openai/whisper-small", cache_dir=cache, local_files_only=True)
        processor = AutoProcessor.from_pretrained(path, local_files_only=True)
        processor.feature_extractor.return_attention_mask = True
        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            path, local_files_only=True, attn_implementation="eager")
        model.generation_config.forced_decoder_ids = None
        _recognizer = pipeline("automatic-speech-recognition", model=model,
                               tokenizer=processor.tokenizer,
                               feature_extractor=processor.feature_extractor, device="cpu")
    return _recognizer({"raw": audio, "sampling_rate": 16000}, chunk_length_s=30,
                       return_timestamps="word", generate_kwargs={"language": language,
                       "task": "transcribe", "num_beams": 1})


def validate_sample(user_id, sample_id):
    item = read_sample(user_id, sample_id)
    try:
        item.update(status="analyzing", error=None)
        save_sample(user_id, item)
        path = directory(user_id) / item["filename"]
        wave = decode(path, 16000)
        duration = len(wave) / 16000
        frames = [part for part in np.array_split(wave, max(1, int(duration * 10))) if len(part)]
        active = sum(float(np.sqrt(np.mean(part ** 2))) > 0.008 for part in frames)
        quality = {"duration": round(duration, 2),
                   "active_seconds": round(duration * active / len(frames), 2),
                   "clipped_fraction": float(np.mean(np.abs(wave) >= 0.99)),
                   "rms": float(np.sqrt(np.mean(wave ** 2)))}
        issues = []
        if not 8 <= duration <= 90:
            issues.append("La toma debe durar entre 8 y 90 segundos.")
        if item.get("guide") and abs(duration - item["guide"]["duration"]) > 1.5:
            issues.append("La toma debe cubrir el fragmento completo de la guia, sin la cuenta atras.")
        if quality["rms"] < 0.008:
            issues.append("Nivel de voz demasiado bajo.")
        if quality["clipped_fraction"] > 0.005:
            issues.append("La grabacion esta saturada.")
        if active / len(frames) < 0.45:
            issues.append("La toma contiene demasiado silencio.")
        item.update(quality=quality, issues=issues, sha256=fingerprint(path))
        if issues:
            item["status"] = "rejected"
        else:
            result = transcribe(wave, item["language"])
            chunks = [{"text": chunk["text"].strip(), "start": chunk["timestamp"][0],
                       "end": chunk["timestamp"][1]} for chunk in result.get("chunks", [])]
            expected = item["expected_text"] if item.get("guide") else get_prompt(item["prompt_id"])["text"]
            item.update(transcript=result["text"], alignment=compare_words(
                expected, chunks), status="review")
        save_sample(user_id, item)
    except Exception as error:
        item.update(status="failed", error=f"No se pudo validar la toma: {error}")
        save_sample(user_id, item)


def dataset_status(user_id):
    samples = list_samples(user_id)
    accepted = [item for item in samples if item["status"] == "accepted"]
    seconds = {language: sum(item["quality"]["active_seconds"] for item in accepted
                           if item["language"] == language) for language in ("es", "en")}
    singing = sum(item["quality"]["active_seconds"] for item in accepted if item["mode"] == "sing")
    prompts = {language: len({item["prompt_id"] for item in accepted if item["language"] == language})
               for language in ("es", "en")}
    missing = []
    if sum(seconds.values()) < 600:
        missing.append("Se requieren 10 minutos de voz activa aceptada.")
    for language, label in (("es", "español"), ("en", "inglés")):
        if seconds[language] < 120 or prompts[language] < 6:
            missing.append(f"Faltan al menos 2 minutos y 6 textos distintos en {label}.")
    if singing < 240:
        missing.append("Se requieren al menos 4 minutos de muestras cantadas.")
    return {"samples": samples, "seconds": seconds, "singing_seconds": singing,
            "prompt_counts": prompts, "ready": not missing, "missing": missing,
            "minimum_seconds": 600}


def dataset_fingerprint(user_id):
    import hashlib
    accepted = sorted((item["id"], item["sha256"]) for item in list_samples(user_id)
                      if item["status"] == "accepted")
    return hashlib.sha256(json.dumps(accepted).encode()).hexdigest()


def recover_samples():
    for path in PROFILES.glob("*/samples/*.json"):
        item = json.loads(path.read_text())
        if item["status"] in {"queued", "analyzing"}:
            item.update(status="failed", error="La revision se interrumpio. Reintenta el analisis.")
            save_sample(path.parents[1].name, item)
