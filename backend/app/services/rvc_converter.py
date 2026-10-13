from pathlib import Path
import subprocess
import sys
import os

import numpy as np
import soundfile as sf

from app.services.audio import decode
from app.services.personal_model import APPLIO, require_model, validate_model_files
from app.config import BACKEND


def convert_personal(source, user_id, pitch_shift, destination, approved=True, progress=None,
                     expected_model=None):
    model = require_model(user_id, approved)
    if expected_model and any(model.get(key) != expected_model.get(key)
                              for key in ("version", "checkpoint_sha256", "dataset_sha256")):
        raise ValueError("El modelo personal cambio. Solicita de nuevo la conversion.")
    return convert_with_model(source, model, pitch_shift, destination, progress)


def convert_snapshot_preview(source, model, pitch_shift, destination):
    return convert_with_model(source, validate_model_files(model), pitch_shift, destination)


def convert_with_model(source, model, pitch_shift, destination, progress=None):
    if progress:
        progress(40, "Convirtiendo con tu modelo RVC personal")
    temporary = Path(destination).with_suffix(".rvc.wav")
    temporary.unlink(missing_ok=True)
    command = [sys.executable, "-u", str(BACKEND / "rvc_infer.py"), "--source", str(source),
               "--destination", str(temporary), "--checkpoint", model["checkpoint"],
               "--index", model["index"], "--pitch", str(pitch_shift)]
    log = Path(destination).with_suffix(".conversion.log")
    with log.open("w") as stream:
        subprocess.run(command, cwd=APPLIO, stdout=stream, stderr=subprocess.STDOUT,
                       env={**os.environ, "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD": "1",
                            "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                            "PYTORCH_ENABLE_MPS_FALLBACK": "1"}, check=True,
                       timeout=max(300, int(sf.info(source).duration * 10)))
    if not temporary.is_file():
        raise RuntimeError(f"RVC no genero audio. Consulta {log.name}.")
    original = decode(source, 44100)
    converted = decode(temporary, 44100)
    converted = np.pad(converted[:len(original)], (0, max(0, len(original) - len(converted))))
    if not np.isfinite(converted).all() or np.sqrt(np.mean(converted ** 2)) < 0.001:
        raise RuntimeError("RVC genero una pista vocal vacia o invalida.")
    correlation = float(np.corrcoef(original, converted)[0, 1])
    if not np.isfinite(correlation) or abs(correlation) > 0.98:
        raise RuntimeError("RVC no produjo una sustitucion vocal valida.")
    sf.write(destination, converted, 44100, subtype="PCM_16")
    temporary.unlink()
    return {"engine": model["engine"], "model_version": model["version"],
            "checkpoint_sha256": model["checkpoint_sha256"], "pitch_shift": pitch_shift,
            "source_output_correlation": correlation, "source_samples": len(original),
            "converted_samples": len(converted), "personal_model": True}
