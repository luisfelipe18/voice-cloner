import hashlib
import io
import json
import math
import subprocess
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np
import parselmouth
import soundfile as sf

from app.config import PROFILES, localize_path, safe_id
from app.services import samples
from app.services.audio import decode, fingerprint

lock = threading.RLock()


def directory(user_id):
    return PROFILES / safe_id(user_id) / "singing-guides"


def read_state(user_id):
    path = directory(user_id) / "catalog.json"
    return json.loads(path.read_text()) if path.is_file() else {
        "status": "missing", "guides": [], "warnings": [], "progress": 0}


def save_state(user_id, state):
    root = directory(user_id)
    root.mkdir(parents=True, exist_ok=True)
    with lock:
        path = root / "catalog.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        temporary.replace(path)


def sources():
    from app.api.songs import library, local_songs
    from app.services.jobs import read_job
    result = []
    local = local_songs()
    listing = library()
    for song in listing["songs"] + listing["uploads"]:
        job = read_job(song["song_id"]) or {}
        source = local.get(song["song_id"]) or localize_path(job.get("source", ""))
        if song.get("language") not in {"es", "en"} or not source.is_file():
            continue
        result.append({"song_id": song["song_id"], "name": song["name"],
                       "language": song["language"], "source": str(source), "job": job})
    return result


def public_state(user_id):
    with lock:
        state = read_state(user_id)
    songs = available_sources(user_id)
    active = {song["song_id"]: song["language"] for song in songs}
    selection = set(state.get("selection_ids", [guide["id"] for guide in state["guides"]]))
    state["guides"] = [guide for guide in state["guides"]
                       if active.get(guide["song_id"]) == guide["language"]
                       and (guide.get("manual") or (guide["id"] in selection and guide.get("word_count", 0) >= 8))]
    state["songs"] = [{key: song[key] for key in ("song_id", "name", "language")} for song in songs]
    return state


def available_sources(user_id):
    state = read_state(user_id)
    excluded = set(state.get("excluded_song_names", []))
    return [song for song in sources() if song["name"].casefold() not in excluded]


def pitch_values(wave):
    return parselmouth.Sound(wave, sampling_frequency=16000).to_pitch(
        time_step=0.02, pitch_floor=65, pitch_ceiling=700).selected_array["frequency"]


def pitch_summary(values):
    voiced = values[values > 0]
    if not len(voiced):
        return None
    low, median, high = np.percentile(voiced, [5, 50, 95])
    return {"low_hz": round(float(low), 1), "median_hz": round(float(median), 1),
            "high_hz": round(float(high), 1)}


def personal_range(user_id):
    values = []
    for item in samples.list_samples(user_id):
        if item["status"] == "accepted" and item.get("mode") == "sing":
            try:
                values.extend(pitch_values(decode(samples.directory(user_id) / item["filename"], 16000)))
            except (ValueError, OSError):
                continue
    return pitch_summary(np.asarray(values))


def recommended_pitch(reference, personal):
    if not reference or not personal:
        return 0
    return max(-12, min(0, round(12 * math.log2(personal["median_hz"] / reference["median_hz"]))))


def song_analysis(song):
    source = Path(song["source"])
    signature = fingerprint(source)
    proof = song["job"].get("proof", {})
    vocals = localize_path(proof.get("original_vocals", ""))
    isolated = vocals.is_file() and proof.get("source_sha256") == signature
    wave = decode(vocals if isolated else source, 16000)
    return {**song, "sha256": signature, "wave": wave, "pitch_values": pitch_values(wave),
            "isolated": isolated, "duration": len(wave) / 16000}


def candidate_windows(song, duration=24):
    length = song["duration"]
    if length < duration:
        return []
    candidates = []
    for start in np.arange(0, length - duration + 0.01, 4):
        wave = song["wave"][int(start * 16000):int((start + duration) * 16000)]
        values = song["pitch_values"][int(start / .02):int((start + duration) / .02)]
        voiced = np.mean(values > 0) if len(values) else 0
        energy = float(np.sqrt(np.mean(wave ** 2)))
        if voiced < .4 or energy < .01:
            continue
        summary = pitch_summary(values)
        spread = min(1, math.log2(summary["high_hz"] / summary["low_hz"]))
        candidates.append({"start": float(start), "duration": duration,
                           "score": float(voiced + .2 * spread), "range": summary})
    # Separate windows avoid collecting the same phrase twice.
    selected = []
    for window in sorted(candidates, key=lambda item: item["score"], reverse=True):
        if all(abs(window["start"] - other["start"]) >= duration + 4 for other in selected):
            selected.append(window)
        if len(selected) == 6:
            break
    return selected


def extract_guide(user_id, song, start, duration, personal, manual=False):
    if start + duration > song["duration"] + .05:
        raise ValueError("El fragmento termina fuera de la cancion.")
    signature = hashlib.sha256(f"{song['sha256']}:{song['language']}:{start:.2f}:{duration:.2f}".encode()).hexdigest()[:16]
    guide_id = "song-" + signature
    state = read_state(user_id)
    existing = next((guide for guide in state["guides"] if guide["id"] == guide_id), None)
    if existing and not manual and existing.get("word_count", 0) < 8:
        raise ValueError("Pocas palabras distintas para una muestra de entrenamiento.")
    wave = song["wave"][round(start * 16000):round((start + duration) * 16000)]
    result = None if existing else samples.transcribe(wave, song["language"])
    text = existing["text"] if existing else result.get("text", "").strip()
    words = existing["words"] if existing else [{"text": chunk["text"].strip(), "start": chunk["timestamp"][0],
              "end": chunk["timestamp"][1]} for chunk in result.get("chunks", [])]
    if len(samples.tokens(text)) < 5:
        raise ValueError("No se reconocieron suficientes palabras en este fragmento.")
    if not manual and len(set(samples.tokens(text))) < 8:
        raise ValueError("Pocas palabras distintas para una muestra de entrenamiento.")
    values = song["pitch_values"][int(start / .02):int((start + duration) / .02)]
    reference = pitch_summary(values)
    root = directory(user_id)
    root.mkdir(parents=True, exist_ok=True)
    if not existing or existing.get("audio_version") != 1 or not (root / f"{guide_id}-mix.flac").is_file():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-i", song["source"],
                        "-t", str(duration), "-ar", "22050", "-ac", "1", "-sample_fmt", "s16",
                        str(root / f"{guide_id}-mix.flac")], check=True, capture_output=True)
        if song["isolated"]:
            sf.write(root / f"{guide_id}-vocal.flac", wave, 16000)
    return {"id": guide_id, "song_id": song["song_id"], "name": song["name"],
            "language": song["language"], "source_sha256": song["sha256"],
            "start_seconds": start, "duration": duration, "text": text, "words": words,
            "word_count": len(set(samples.tokens(text))), "range": reference,
            "audio_version": 1, "manual": manual,
            "isolated_available": song["isolated"], "range_estimated": True,
            "recommended_pitch": recommended_pitch(reference, personal)}


def prepare(user_id, song_id=None, start=0, duration=24, exclude_song_ids=None, avoid_song_ids=None):
    with lock:
        state = read_state(user_id)
        if state["status"] in {"queued", "preparing"}:
            raise ValueError("Las guias ya estan en preparacion.")
        songs = sources()
        excluded = set(state.get("excluded_song_names", []))
        excluded.update(song["name"].casefold() for song in songs if song["song_id"] in (exclude_song_ids or []))
        songs = [song for song in songs if song["name"].casefold() not in excluded]
        if not songs:
            raise ValueError("Añade canciones y selecciona su idioma antes de preparar guias.")
        if song_id is not None and not any(song["song_id"] == song_id for song in songs):
            raise ValueError("Cancion no disponible o excluida de las guias.")
        state["excluded_song_names"] = sorted(excluded)
        state.update(status="queued", progress=0, error=None, warnings=[], stage="En cola")
        save_state(user_id, state)
        # Recognition shares the sample-review queue: one Whisper invocation at a time.
        samples.validator.submit(run_preparation, user_id, songs, state, song_id, start, duration, avoid_song_ids or [])
    return public_state(user_id)


def run_preparation(user_id, songs, state, song_id=None, start=0, duration=24, avoid_song_ids=None):
    try:
        state.update(status="preparing", stage="Midiendo tu rango cantado")
        save_state(user_id, state)
        personal = personal_range(user_id)
        state["personal_range"] = personal
        analysis, seen = [], set()
        for index, song in enumerate(songs):
            if song_id and song["song_id"] != song_id:
                continue
            if not song_id and song["song_id"] in (avoid_song_ids or []):
                continue
            state.update(stage=f"Buscando tramos con voz · {song['name']}", progress=round(20 * index / len(songs)))
            save_state(user_id, state)
            try:
                item = song_analysis(song)
                if item["sha256"] not in seen:
                    analysis.append(item)
                    seen.add(item["sha256"])
            except Exception as error:
                state["warnings"].append(f"{song['name']}: {error}")
        chosen = analysis
        if not song_id:
            chosen = []
            for language in ("es", "en"):
                ordered = sorted([song for song in analysis if song["language"] == language],
                                 key=lambda song: (pitch_summary(song["pitch_values"]) or {}).get("median_hz", 0))
                chosen.extend(ordered)
        existing_ids = {guide["id"] for guide in state["guides"]}
        selected_ids = set(state.get("selection_ids", [])) if song_id else set()
        for index, song in enumerate(chosen):
            state.update(stage=f"Reconociendo fragmentos · {song['name']}",
                         progress=20 + round(75 * index / max(1, len(chosen))))
            save_state(user_id, state)
            windows = [{"start": start, "duration": duration}] if song_id else candidate_windows(song)
            found = 0
            for window in windows:
                try:
                    guide = extract_guide(user_id, song, window["start"], window["duration"], personal, manual=bool(song_id))
                    if guide["id"] not in existing_ids:
                        state["guides"].append(guide)
                        existing_ids.add(guide["id"])
                    else:
                        state["guides"] = [guide if item["id"] == guide["id"] else item for item in state["guides"]]
                    found += 1
                    selected_ids.add(guide["id"])
                    save_state(user_id, state)
                    break
                except Exception as error:
                    state["warnings"].append(f"{song['name']}: {error}")
            if not found:
                state["warnings"].append(f"{song['name']}: no hay un fragmento cantado valido; elige otro tramo.")
                if song_id:
                    raise ValueError(state["warnings"][-1])
        if not selected_ids:
            raise ValueError("No se pudo preparar ningun fragmento con voz. Selecciona un tramo manualmente.")
        state["selection_ids"] = sorted(selected_ids)
        state.update(status="ready", progress=100, stage="Guias listas")
    except Exception as error:
        state.update(status="failed", error=str(error))
    finally:
        save_state(user_id, state)


def get_guide(user_id, guide_id):
    safe_id(guide_id)
    guide = next((item for item in read_state(user_id)["guides"] if item["id"] == guide_id), None)
    if not guide:
        raise ValueError("Guia no encontrada.")
    source = next((song for song in available_sources(user_id) if song["song_id"] == guide["song_id"]), None)
    if not source or source["language"] != guide["language"] or fingerprint(Path(source["source"])) != guide["source_sha256"]:
        raise ValueError("La cancion de esta guia cambio o ya no esta en la biblioteca.")
    return guide


@lru_cache(maxsize=8)
def shifted_audio(path, mtime, pitch):
    import librosa
    wave, rate = sf.read(path, always_2d=True, dtype="float32")
    shifted = librosa.effects.pitch_shift(wave.T, sr=rate, n_steps=pitch).T
    shifted = np.clip(shifted, -.98, .98)
    output = io.BytesIO()
    sf.write(output, shifted, rate, format="FLAC")
    return output.getvalue()


def guide_audio(user_id, guide_id, pitch=0, kind="mix"):
    guide = get_guide(user_id, guide_id)
    if kind not in {"mix", "vocal"} or (kind == "vocal" and not guide["isolated_available"]):
        raise ValueError("Pista guia no disponible.")
    path = directory(user_id) / f"{guide_id}-{kind}.flac"
    if not path.is_file():
        raise ValueError("Audio guia no encontrado. Prepara las guias de nuevo.")
    return shifted_audio(str(path), path.stat().st_mtime_ns, pitch) if pitch else path


def recover():
    for path in PROFILES.glob("*/singing-guides/catalog.json"):
        state = json.loads(path.read_text())
        if state["status"] in {"queued", "preparing"}:
            state.update(status="failed", error="La preparacion se interrumpio. Puedes reintentar.")
            save_state(path.parents[1].name, state)
