import json
import re
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import soundfile as sf

from app.config import OUTPUTS, STORAGE, safe_id
from app.services.audio import decode, fingerprint
from app.services.personal_model import require_model
from app.services.rvc_converter import convert_personal
from app.services.samples import tokens, transcribe, validator

lock = threading.RLock()
renderer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="performance-mix")


def directory(song_id):
    return STORAGE / "performances" / safe_id(song_id)


def read(song_id):
    path = directory(song_id) / "session.json"
    return json.loads(path.read_text()) if path.is_file() else {
        "song_id": song_id, "lyrics": "", "language": "es", "takes": [],
        "selected": {}, "backing": {"status": "missing"}, "exports": [],
    }


def save(item):
    root = directory(item["song_id"])
    root.mkdir(parents=True, exist_ok=True)
    path = root / "session.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(item, ensure_ascii=False, indent=2))
    temporary.replace(path)


def take_from(item, take_id):
    return next((take for take in item["takes"] if take["id"] == take_id), None)


def word_alignment(expected, chunks, duration):
    # ASR matches are evidence, not forced phoneme alignment. Missing times are estimates.
    wanted = [(match.group(), tokens(match.group().replace("\u2019", "'"))[0]) for match in
              re.finditer(r"[^\W\d_]+(?:['\u2019][^\W\d_]+)?", expected, re.UNICODE)
              if tokens(match.group().replace("\u2019", "'"))]
    heard = []
    for chunk in chunks:
        timestamp = chunk.get("timestamp", (None, None))
        words = tokens(chunk.get("text", "").replace("\u2019", "'"))
        for word in words:
            heard.append({"token": word, "start": timestamp[0], "end": timestamp[1],
                          "estimated": len(words) != 1})
    rows = [{"index": index, "text": word, "status": "missing", "start": None,
             "end": None, "estimated": True} for index, (word, _) in enumerate(wanted)]
    matcher = SequenceMatcher(None, [token for _, token in wanted],
                              [word["token"] for word in heard], autojunk=False)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            heard_word = heard[block.b + offset]
            row = rows[block.a + offset]
            if heard_word["start"] is not None and heard_word["end"] is not None:
                row.update(status="matched", start=max(0, float(heard_word["start"])),
                           end=min(duration, float(heard_word["end"])),
                           estimated=heard_word["estimated"])
            else:
                row["status"] = "uncertain"
    for index, row in enumerate(rows):
        if row["start"] is not None:
            continue
        previous = next((word for word in reversed(rows[:index])
                         if word["status"] == "matched"), None)
        following = next((word for word in rows[index + 1:]
                          if word["status"] == "matched"), None)
        left_index = previous["index"] if previous else -1
        right_index = following["index"] if following else len(rows)
        left = previous["end"] if previous else 0
        right = following["start"] if following else duration
        width = max(0, right - left) / max(1, right_index - left_index - 1)
        start = left + width * (index - left_index - 1)
        row.update(start=round(start, 3), end=round(min(duration, start + width), 3))
    return rows


def resolved_words(item, take):
    rows = [dict(word) for word in take.get("words", [])]
    for row in rows:
        correction = next((other for other in reversed(item["takes"])
                           if other.get("parent_id") == take["id"]
                           and other.get("word_index") == row["index"]
                           and other.get("approved")), None)
        if correction:
            row.update(status="corrected", correction_id=correction["id"])
        elif row["index"] in take.get("confirmed_words", []):
            row["status"] = "confirmed"
    return rows


def public(item):
    result = {**item, "takes": []}
    for take in item["takes"]:
        value = {key: value for key, value in take.items() if key not in {"filename", "sha256"}}
        value["audio_url"] = f"/api/performances/{item['song_id']}/takes/{take['id']}/audio"
        value["words"] = resolved_words(item, take)
        value["stale"] = take.get("lyrics") != item["lyrics"] or take["language"] != item["language"]
        result["takes"].append(value)
    result["backing"] = {key: value for key, value in item["backing"].items() if key != "path"}
    if item["backing"].get("status") == "ready":
        result["backing"]["url"] = f"/api/performances/{item['song_id']}/backing/audio"
    result["exports"] = [{key: value for key, value in export.items() if key not in {"path", "vocal_path"}}
                         for export in item["exports"]]
    return result


def analyze(song_id, take_id):
    with lock:
        item = read(song_id)
        take = take_from(item, take_id)
        take.update(status="analyzing", error=None)
        save(item)
    try:
        audio = decode(directory(song_id) / take["filename"], 16000)
        result = transcribe(audio, take["language"])
        words = word_alignment(take["expected_text"], result.get("chunks", []), take["duration"])
        fields = {"status": "review", "transcript": result["text"], "words": words}
    except Exception as error:
        fields = {"status": "failed", "error": f"Reconocimiento fallido: {error}"}
    with lock:
        item = read(song_id)
        take_from(item, take_id).update(fields)
        save(item)


def prepare_backing(song_id, source):
    try:
        from app.services.separator import separate_song
        stems = separate_song(str(source), str(directory(song_id) / f"backing-{uuid.uuid4().hex[:12]}"))
        fields = {"status": "ready", "path": stems["instrumental"], "kind": "separated",
                  "source_sha256": fingerprint(source)}
    except Exception as error:
        fields = {"status": "failed", "error": str(error)}
    with lock:
        item = read(song_id)
        item["backing"] = fields
        save(item)


def replace_word(audio, replacement, start, end, rate=44100):
    left, right = round(start * rate), round(end * rate)
    if not 0 <= left < right <= len(audio):
        raise ValueError("El intervalo de la palabra no es valido.")
    target = right - left
    ratio = len(replacement) / target
    if not 0.5 <= ratio <= 2:
        raise ValueError("La repeticion y el intervalo difieren demasiado. Revisa los tiempos de la palabra.")
    if len(replacement) != target:
        import librosa
        replacement = librosa.effects.time_stretch(replacement, rate=ratio)
    replacement = np.pad(replacement[:target], (0, max(0, target - len(replacement))))
    fade = min(round(rate * 0.015), target // 4)
    weights = np.ones(target, dtype=np.float32)
    if fade:
        weights[:fade] = np.linspace(0, 1, fade)
        weights[-fade:] = np.linspace(1, 0, fade)
    audio[left:right] = audio[left:right] * (1 - weights) + replacement * weights


def update_export(song_id, export_id, **fields):
    with lock:
        item = read(song_id)
        next(export for export in item["exports"] if export["id"] == export_id).update(fields)
        save(item)


def render_mix(song_id, export_id, snapshot, take_id, settings, model):
    root = directory(song_id) / "exports" / export_id
    root.mkdir(parents=True, exist_ok=True)
    try:
        update_export(song_id, export_id, progress=10, stage="Preparando tu interpretacion")
        take = take_from(snapshot, take_id)
        vocal = decode(directory(song_id) / take["filename"])
        corrections = []
        for word in resolved_words(snapshot, take):
            if word.get("correction_id"):
                correction = take_from(snapshot, word["correction_id"])
                replace_word(vocal, decode(directory(song_id) / correction["filename"]),
                             correction["target_start"], correction["target_end"])
                corrections.append(correction["id"])
        sf.write(root / "patched.wav", vocal, 44100, subtype="PCM_24")
        filters = "highpass=f=70,lowpass=f=15000,acompressor=threshold=0.125:ratio=2:attack=10:release=120:makeup=1"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(root / "patched.wav"),
                        "-af", filters if settings["cleanup"] else "anull",
                        str(root / "clean.wav")], check=True, capture_output=True)
        converted = root / "personal_vocals.flac"
        conversion = convert_personal(
            root / "clean.wav", settings["user_id"], settings["pitch_shift"], converted,
            expected_model=model,
            progress=lambda progress, stage: update_export(song_id, export_id,
                                                           progress=progress, stage=stage))
        if not conversion.get("personal_model") or conversion.get("model_version") != model["version"]:
            raise RuntimeError("La conversion no corresponde al modelo personal solicitado.")
        update_export(song_id, export_id, progress=85, stage="Mezclando la voz convertida")
        offset = take["song_start"] + settings["offset_ms"] / 1000
        delay = max(0, round(offset * 1000))
        trim = f"atrim=start={max(0, -offset)},asetpts=PTS-STARTPTS,"
        gain = 10 ** (settings["vocal_gain_db"] / 20)
        backing_gain = 10 ** (settings["backing_gain_db"] / 20)
        destination = OUTPUTS / "interpretations" / f"{safe_id(song_id)}-{export_id}.mp3"
        destination.parent.mkdir(parents=True, exist_ok=True)
        encoded = root / "mix.mp3"
        graph = (f"[0:a]{trim}volume={gain},adelay={delay}:all=1[v];"
                 f"[1:a]volume={backing_gain}[b];[b][v]amix=inputs=2:duration=first:"
                 "dropout_transition=0:normalize=0,alimiter=limit=0.97:level=false[out]")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(converted),
                        "-i", snapshot["backing"]["path"], "-filter_complex", graph,
                        "-map", "[out]", "-ar", "44100", "-ac", "2", "-b:a", "320k",
                        str(encoded)], check=True, capture_output=True)
        proof = {"engine": "personal-rvc-performance", "song_id": song_id, "take_id": take_id,
                 "corrections": corrections, "settings": settings,
                 "conversion": conversion, "dataset_sha256": model.get("dataset_sha256"),
                 "source_sha256": take["sha256"], "conversion_source_sha256": fingerprint(root / "clean.wav"),
                 "converted_vocals_sha256": fingerprint(converted), "output_sha256": fingerprint(encoded),
                 "backing_kind": snapshot["backing"]["kind"], "lyrics": take["lyrics"]}
        destination.with_suffix(".json").write_text(json.dumps(proof, ensure_ascii=False, indent=2))
        encoded.replace(destination)
        fields = {"status": "completed", "progress": 100, "stage": "Modelo aplicado y mezcla lista",
                  "path": str(destination), "vocal_path": str(converted), "conversion": conversion,
                  "url": f"/api/performances/{song_id}/exports/{export_id}/audio",
                  "vocal_url": f"/api/performances/{song_id}/exports/{export_id}/vocals"}
    except Exception as error:
        fields = {"status": "failed", "stage": "Conversion o mezcla fallida",
                  "error": f"No se pudo aplicar tu modelo y mezclar: {error}"}
    update_export(song_id, export_id, **fields)


def recover():
    for path in (STORAGE / "performances").glob("*/session.json"):
        with lock:
            item = json.loads(path.read_text())
            for take in item["takes"]:
                if take["status"] in {"queued", "analyzing"}:
                    take.update(status="failed", error="El reconocimiento se interrumpio. Puedes reintentarlo.")
            if item["backing"]["status"] == "processing":
                item["backing"].update(status="failed", error="Separacion interrumpida.")
            for export in item["exports"]:
                if export["status"] == "processing":
                    export.update(status="failed", error="Mezcla interrumpida.")
            save(item)
