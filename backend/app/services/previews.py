from pathlib import Path
import uuid

import soundfile as sf

from app.config import PROFILES, safe_id
from app.services.audio import decode
from app.services.mixer import mix_final_song
from app.services.personal_model import trainer, model_lock, require_model, save_model
from app.services.rvc_converter import convert_personal, convert_snapshot_preview
from app.services.separator import separate_song


def start_preview(user_id, source, start=30, language="es", pitch_shift=0):
    with model_lock:
        state = require_model(user_id, approved=False)
        if state.get("preview", {}).get("status") == "processing":
            raise ValueError("Ya hay una prueba en curso.")
        state.update(approved=False, preview={"status": "processing", "id": uuid.uuid4().hex[:12], "song": Path(source).name,
                                             "start_seconds": start, "language": language, "pitch_shift": pitch_shift})
        save_model(user_id, state)
        trainer.submit(run_preview, user_id, source, start, state)
        return state


def run_preview(user_id, source, start, state):
    try:
        root = PROFILES / safe_id(user_id) / "models" / state["version"] / "preview" / state["preview"]["id"]
        root.mkdir(parents=True, exist_ok=True)
        audio = decode(source, 44100)
        clip = audio[int(start * 44100):int((start + 15) * 44100)]
        if len(clip) < 44100 * 3:
            raise ValueError("El punto elegido no contiene suficiente audio.")
        original = root / "original.wav"
        sf.write(original, clip, 44100, subtype="PCM_16")
        stems = separate_song(str(original), str(root))
        converted = root / "converted.flac"
        metrics = convert_personal(stems["vocals"], user_id, state["preview"].get("pitch_shift", 0), converted, approved=False)
        output = root / "preview.mp3"
        mix_final_song(converted, stems["vocals"], stems["instrumental"], output)
        if state.get("base_model"):
            previous_vocals = root / "previous.flac"
            previous_metrics = convert_snapshot_preview(stems["vocals"], state["base_model"],
                state["preview"].get("pitch_shift", 0), previous_vocals)
            previous_output = root / "previous.mp3"
            mix_final_song(previous_vocals, stems["vocals"], stems["instrumental"], previous_output)
            state["preview"].update(previous_output=str(previous_output), previous_metrics=previous_metrics)
        state["preview"].update(status="completed", output=str(output), original=str(original), metrics=metrics)
    except Exception as error:
        state["preview"].update(status="failed", error=str(error))
    save_model(user_id, state)
