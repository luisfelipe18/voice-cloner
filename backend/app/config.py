import re
import os
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
STORAGE = BACKEND / "storage"
PROFILES = STORAGE / "profiles"
JOBS = STORAGE / "jobs"
MODELS = BACKEND / "models"
ORIGINALS = ROOT / "songs-original"
OUTPUTS = ROOT / "songs-voice-replaced"
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".webm"}
os.environ["HF_HOME"] = str(MODELS / "huggingface")
os.environ["HF_HUB_CACHE"] = str(MODELS / "huggingface/hub")
os.environ["MPLCONFIGDIR"] = str(STORAGE / "cache/matplotlib")
os.environ["TORCH_HOME"] = str(MODELS / "torch")
os.environ["XDG_CACHE_HOME"] = str(STORAGE / "cache")


def safe_id(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", value):
        raise ValueError("Identificador no valido.")
    return value


def localize_path(value: str) -> Path:
    path = Path(value)
    if path.exists():
        return path
    # Persisted paths may have been created on the host before mounting Docker.
    for anchor, directory in (("songs-original", ORIGINALS),
                               ("songs-voice-replaced", OUTPUTS), ("storage", STORAGE), ("Applio", BACKEND / "Applio")):
        if anchor in path.parts:
            position = path.parts.index(anchor)
            remainder = path.parts[position + 1:]
            if ".." not in remainder:
                return directory.joinpath(*remainder)
    return path
