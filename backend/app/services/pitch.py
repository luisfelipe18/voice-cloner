import os
import tempfile
from pathlib import Path

import numpy as np
import parselmouth

from app.services.audio import decode


def analyze_path(path: Path) -> dict:
    audio = decode(path, 16000)
    sound = parselmouth.Sound(audio, sampling_frequency=16000)
    values = sound.to_pitch(pitch_floor=65, pitch_ceiling=700).selected_array["frequency"]
    voiced = values[values > 0]
    if not voiced.size:
        raise ValueError("No se detecto voz afinada en el audio.")
    median = float(np.median(voiced))
    return {
        "duration_seconds": round(len(audio) / 16000, 2),
        "f0_median_hz": round(median, 2),
        "f0_p5_hz": round(float(np.percentile(voiced, 5)), 2),
        "f0_p95_hz": round(float(np.percentile(voiced, 95)), 2),
        "register": "grave" if median < 165 else "medio" if median < 250 else "agudo",
    }


def analyze_voice(audio_bytes: bytes) -> dict:
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(audio_bytes)
        path = Path(tmp.name)
    try:
        return analyze_path(path)
    finally:
        os.unlink(path)
