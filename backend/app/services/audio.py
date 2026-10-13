import hashlib
import subprocess
from pathlib import Path

import numpy as np


def fingerprint(path: Path) -> str:
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def decode(path: Path, sample_rate: int = 44100, max_seconds: float | None = None) -> np.ndarray:
    limit = ["-t", str(max_seconds)] if max_seconds is not None else []
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), *limit, "-f", "f32le",
         "-ac", "1", "-ar", str(sample_rate), "pipe:1"],
        capture_output=True, check=True,
    )
    audio = np.frombuffer(result.stdout, dtype=np.float32).copy()
    if audio.size == 0 or not np.isfinite(audio).all():
        raise ValueError(f"Audio vacio o invalido: {path.name}")
    return audio
