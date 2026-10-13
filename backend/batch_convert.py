import argparse
import json
import sys

from app.config import AUDIO_EXTENSIONS, ORIGINALS, OUTPUTS
from app.services.audio import fingerprint
from app.services.jobs import run_job, save_job, read_job
from app.services.personal_model import require_model
from app.api.songs import song_languages


def library_id(path):
    import hashlib
    return "local-" + hashlib.sha256(path.name.encode()).hexdigest()[:12]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="luis_felipe")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    failures = []
    try:
        model = require_model(args.user)
    except ValueError as error:
        raise SystemExit(str(error))
    languages = song_languages()
    files = sorted(p for p in ORIGINALS.iterdir() if p.suffix.lower() in AUDIO_EXTENSIONS)
    if not files:
        raise SystemExit("No hay canciones en songs-original.")
    if any(library_id(path) not in languages for path in files):
        raise SystemExit("Selecciona el idioma de cada cancion en el estudio primero.")
    for index, path in enumerate(files):
        output = OUTPUTS / f"{path.stem}.mp3"
        proof_path = output.with_suffix(".json")
        if not args.force and output.is_file() and proof_path.is_file():
            proof = json.loads(proof_path.read_text())
            if (proof.get("source_sha256") == fingerprint(path)
                    and proof.get("output_sha256") == fingerprint(output)
                    and proof.get("user_id") == args.user
                    and proof.get("model_version") == model["version"]
                    and proof.get("language") == languages[library_id(path)]
                    and proof.get("dataset_sha256") == model["dataset_sha256"]):
                print(f"[{index + 1}/{len(files)}] Ya completada: {path.name}", flush=True)
                continue
        song_id = library_id(path)
        existing = read_job(song_id)
        if existing and existing["status"] in {"queued", "processing"}:
            raise SystemExit("Hay una conversion en curso. Espera a que termine antes de ejecutar el lote CLI.")
        save_job({"song_id": song_id, "source": str(path), "name": path.name,
                  "user_id": args.user, "status": "queued", "progress": 0,
                  "stage": "En cola", "error": None, "pitch_shift": 0,
                  "steps": 30, "reference_kind": "talk", "language": languages[song_id],
                  "output_name": output.name})
        print(f"[{index + 1}/{len(files)}] {path.name}", flush=True)
        run_job(song_id)
        from app.services.jobs import read_job
        job = read_job(song_id)
        print(json.dumps({"name": path.name, "status": job["status"],
                          "error": job.get("error"), "proof": job.get("proof")}), flush=True)
        if job["status"] != "completed":
            failures.append(path.name)
    if failures:
        print("Fallaron: " + ", ".join(failures), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
