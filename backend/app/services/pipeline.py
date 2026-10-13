import json
from pathlib import Path

from app.config import STORAGE, OUTPUTS, localize_path
from app.services.audio import fingerprint
from app.services.rvc_converter import convert_personal as convert_voice
from app.services.mixer import mix_final_song
from app.services.personal_model import require_model
from app.services.separator import separate_song


def process_full_song_pipeline(song_path, song_id, user_id, progress=None,
                               pitch_shift=0, steps=30, reference_kind="talk", output_name=None,
                               language="es"):
    notify = progress or (lambda percent, stage: print(f"{percent}% {stage}", flush=True))
    source = Path(song_path).resolve()
    notify(2, "Validando tu modelo personal aprobado")
    model = require_model(user_id)
    if language not in {"es", "en"}:
        raise ValueError("Selecciona el idioma de la cancion.")
    directory = STORAGE / "processed" / song_id
    directory.mkdir(parents=True, exist_ok=True)
    notify(8, "Separando voz e instrumentos")
    stems_file = directory / "stems.json"
    source_hash = fingerprint(source)
    stems = json.loads(stems_file.read_text()) if stems_file.is_file() else None
    if stems:
        for key in ("vocals", "instrumental"):
            stems[key] = str(localize_path(stems[key]))
    if not stems or stems.get("source_sha256") != source_hash or not all(
        Path(stems[key]).is_file() for key in ("vocals", "instrumental")
    ):
        stems = separate_song(str(source), str(directory))
        stems["source_sha256"] = source_hash
        stems_file.write_text(json.dumps(stems, indent=2))
    notify(30, "Cargando el motor de voz cantada")
    converted = directory / "your_vocals.flac"
    metrics = convert_voice(stems["vocals"], user_id, pitch_shift, str(converted), progress=notify)
    notify(90, "Mezclando tu voz con el instrumental")
    destination = OUTPUTS / (output_name or f"{source.stem}-{user_id}.mp3")
    mix = mix_final_song(converted, stems["vocals"], stems["instrumental"], str(destination))
    proof = {"song_id": song_id, "user_id": user_id, "source": str(source),
             "source_sha256": source_hash, "output": str(destination),
             "output_sha256": fingerprint(destination),
             "dataset_sha256": model["dataset_sha256"], "language": language,
             "model_version": model["version"], "conversion": metrics, "mix": mix,
             "original_vocals": stems["vocals"], "converted_vocals": str(converted)}
    destination.with_suffix(".json").write_text(json.dumps(proof, indent=2))
    notify(100, "Completado")
    return proof
