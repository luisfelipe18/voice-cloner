import json
import os
import random
import re
import signal
import shutil
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import soundfile as sf

from app.config import BACKEND, PROFILES, safe_id, localize_path
from app.services.audio import decode, fingerprint
from app.services.samples import dataset_fingerprint, dataset_status, directory

APPLIO = BACKEND / "Applio"
trainer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="personal-model")
model_lock = threading.RLock()
active_processes = {}
cancelled = set()
FINE_TUNE_EPOCHS = 50
FINE_TUNE_LEARNING_RATE = 0.00002


def state_path(user_id):
    return PROFILES / safe_id(user_id) / "personal_model.json"


def save_model(user_id, state):
    with model_lock:
        path = state_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2))
        temporary.replace(path)
        if state.get("version"):
            snapshot = PROFILES / safe_id(user_id) / "models" / safe_id(state["version"]) / "model.json"
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            temporary = snapshot.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, indent=2))
            temporary.replace(snapshot)


def training_checkpoints(state):
    experiment = APPLIO / "logs" / safe_id(state["version"])
    stored = state.get("training_checkpoints", {})
    paths = {}
    for key, prefix in (("generator", "G"), ("discriminator", "D")):
        if stored.get(key):
            paths[key] = localize_path(stored[key])
        else:
            candidates = list(experiment.glob(f"{prefix}_*.pth"))
            paths[key] = max(candidates, key=lambda path: path.stat().st_mtime) if candidates else experiment / f"{prefix}_missing.pth"
    paths["config"] = localize_path(stored["config"]) if stored.get("config") else experiment / "config.json"
    return paths


def trained_sample_ids(user_id, state):
    if "sample_manifest" in state:
        return set(state["sample_manifest"])
    root = PROFILES / safe_id(user_id) / "models" / safe_id(state["version"])
    return {path.stem for folder in ("dataset", "holdout") for path in (root / folder).glob("*.wav")}


def fine_tune_info(user_id, state):
    if state.get("inference_only"):
        return {"available": False, "changed": False, "reason": "Modelo importado para inferencia. Fine-tuning requiere el paquete privado de entrenamiento."}
    if state.get("status") != "trained" or not state.get("checkpoint") or not state.get("version"):
        return {"available": False, "changed": False, "reason": "Primero entrena un modelo personal."}
    paths = training_checkpoints(state)
    available = all(path.is_file() for path in paths.values())
    changed = state.get("dataset_sha256") != dataset_fingerprint(user_id)
    new_ids = {item["id"] for item in dataset_status(user_id)["samples"] if item["status"] == "accepted"} - trained_sample_ids(user_id, state)
    return {"available": available, "changed": changed, "new_samples": len(new_ids),
            "default_epochs": FINE_TUNE_EPOCHS, "learning_rate": FINE_TUNE_LEARNING_RATE,
            "reason": "" if available else "Faltan los checkpoints completos G/D del modelo personal."}


def model_status(user_id):
    path = state_path(user_id)
    state = json.loads(path.read_text()) if path.is_file() else {"status": "missing", "approved": False}
    for key in ("checkpoint", "index", "log"):
        if state.get(key):
            state[key] = str(localize_path(state[key]))
    for key in ("output", "original"):
        if state.get("preview", {}).get(key):
            state["preview"][key] = str(localize_path(state["preview"][key]))
    if not state.get("inference_only") and state.get("dataset_sha256") and state["dataset_sha256"] != dataset_fingerprint(user_id):
        state = {**state, "approved": False, "stale": True}
    if state["status"] == "training" and state.get("log") and Path(state["log"]).is_file():
        with Path(state["log"]).open("rb") as log:
            log.seek(max(0, Path(state["log"]).stat().st_size - 6000))
            tail = log.read().decode(errors="replace")
        epochs = re.findall(r"epoch=(\d+)", tail)
        if epochs:
            state["epoch"] = int(epochs[-1])
    elif state["status"] == "trained" and state.get("epochs"):
        state["epoch"] = state["epochs"]
    if state.get("preview", {}).get("status") == "completed":
        version = state["preview"].get("id", state.get("version", ""))
        state["preview"]["url"] = f"/api/studio/{user_id}/preview/audio?v={version}"
        state["preview"]["original_url"] = f"/api/studio/{user_id}/preview/original?v={version}"
        if state["preview"].get("previous_output"):
            state["preview"]["previous_url"] = f"/api/studio/{user_id}/preview/previous?v={version}"
    state["fine_tune"] = fine_tune_info(user_id, state)
    return state


def validate_model_files(state):
    if state.get("status") != "trained":
        raise ValueError("El modelo de origen no esta entrenado.")
    result = dict(state)
    for key in ("checkpoint", "index"):
        result[key] = str(localize_path(state.get(key, "")))
        if not Path(result[key]).is_file():
            raise ValueError("No se encuentran los archivos del modelo personal.")
    if fingerprint(result["checkpoint"]) != state.get("checkpoint_sha256"):
        raise ValueError("El checkpoint personal cambio; se requiere volver a validarlo.")
    return result


def require_model(user_id, approved=True):
    state = model_status(user_id)
    if state["status"] != "trained" or state.get("stale"):
        raise ValueError("Falta entrenar un modelo personal con las muestras actuales.")
    if approved and not state.get("approved"):
        raise ValueError("Escucha y aprueba una prueba corta antes de convertir canciones completas.")
    return validate_model_files(state)


def validate_training_source(state):
    try:
        return inspect_training_source(state)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(f"No se pudieron validar los checkpoints de origen: {error}") from error


def inspect_training_source(state):
    import torch
    source = validate_model_files(state)
    paths = training_checkpoints(source)
    if not all(path.is_file() for path in paths.values()):
        raise ValueError("Faltan los checkpoints completos G/D. No se sustituira por un modelo base.")
    exported = torch.load(source["checkpoint"], map_location="cpu", weights_only=True, mmap=True)
    if exported.get("sr") != 40000 or exported.get("version") != "v2" or not exported.get("f0") or exported.get("vocoder") != "HiFi-GAN":
        raise ValueError("El modelo de origen no es compatible con RVC v2/40 kHz/HiFi-GAN.")
    generator = torch.load(paths["generator"], map_location="cpu", weights_only=True, mmap=True)
    discriminator = torch.load(paths["discriminator"], map_location="cpu", weights_only=True, mmap=True)
    epoch = generator.get("iteration")
    if not isinstance(epoch, int) or epoch < 1 or epoch != discriminator.get("iteration") or epoch != exported.get("epoch"):
        raise ValueError("Los checkpoints G/D no corresponden a la misma epoca del modelo personal.")
    for key, weight in exported["weight"].items():
        original = generator["model"].get(key)
        if original is None or original.shape != weight.shape or not torch.equal(original.to(weight.dtype), weight):
            raise ValueError("El generador no coincide con los pesos del modelo personal exportado.")
    config = json.loads(paths["config"].read_text())
    if config["data"]["sample_rate"] != 40000:
        raise ValueError("La configuracion del modelo de origen no es de 40 kHz.")
    signatures = {key + "_sha256": fingerprint(path) for key, path in paths.items()}
    stored = state.get("training_checkpoints", {})
    if any(stored.get(key) and stored[key] != value for key, value in signatures.items()):
        raise ValueError("Un checkpoint de entrenamiento cambio desde su validacion.")
    return {**{key: str(path) for key, path in paths.items()}, **signatures, "epoch": epoch,
            "speakers": generator["model"]["emb_g.weight"].shape[0]}


def model_snapshot(state):
    return {key: value for key, value in state.items() if key not in {"fine_tune", "stale", "base_model", "last_training"}}


def checked_command(arguments, log_path, user_id=None):
    environment = {**os.environ, "PYTORCH_ENABLE_MPS_FALLBACK": "1",
                   "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD": "1", "OMP_NUM_THREADS": "4"}
    with Path(log_path).open("a") as log:
        with model_lock:
            if user_id in cancelled:
                raise RuntimeError("Entrenamiento cancelado.")
            process = subprocess.Popen([sys.executable, "-u", *map(str, arguments)], cwd=APPLIO,
                                       stdout=log, stderr=subprocess.STDOUT, env=environment,
                                       start_new_session=True)
            if user_id:
                active_processes[user_id] = process
        try:
            code = process.wait()
            if code:
                raise RuntimeError(f"El motor termino con codigo {code}. Consulta training.log.")
        finally:
            with model_lock:
                active_processes.pop(user_id, None)


def start_training(user_id, epochs=None, mode=None, batch_size=4):
    with model_lock:
        state = model_status(user_id)
        if state["status"] == "training" or state.get("preview", {}).get("status") == "processing":
            raise ValueError("Ya hay un proceso de entrenamiento o prueba en curso.")
        dataset = dataset_status(user_id)
        if not dataset["ready"]:
            raise ValueError(" ".join(dataset["missing"]))
        mode = mode or ("fine_tune" if state.get("checkpoint") else "initial")
        if mode not in {"initial", "fine_tune"}:
            raise ValueError("Modalidad de entrenamiento no valida.")
        if not isinstance(batch_size, int) or not 1 <= batch_size <= 16:
            raise ValueError("Batch size: 1-16.")
        epochs = epochs if epochs is not None else FINE_TUNE_EPOCHS if mode == "fine_tune" else 200
        if not (10 <= epochs <= 200 if mode == "fine_tune" else 100 <= epochs <= 500):
            raise ValueError("Fine-tuning: 10-200 epocas. Entrenamiento inicial: 100-500 epocas.")
        base = None
        if mode == "fine_tune":
            if state.get("inference_only"):
                raise ValueError("Para fine-tuning importa el paquete privado con muestras y checkpoints G/D.")
            if state.get("status") != "trained":
                raise ValueError("Primero entrena un modelo personal antes del fine-tuning.")
            if state.get("dataset_sha256") == dataset_fingerprint(user_id):
                raise ValueError("No hay cambios en las muestras aceptadas para hacer fine-tuning.")
            source = validate_training_source(state)
            base = model_snapshot(json.loads(state_path(user_id).read_text()))
            save_model(user_id, base)
        pretrained = APPLIO / "rvc/models/pretraineds/hifi-gan"
        if mode == "initial" and not all((pretrained / filename).is_file() for filename in ("f0G40k.pth", "f0D40k.pth")):
            raise ValueError("Faltan los pesos base RVC. Ejecuta ./install_ai_engine.sh.")
        config = APPLIO / "assets/config.json"
        if not config.is_file():
            shutil.copy2(APPLIO / "assets/config_template.json", config)
        version = f"{safe_id(user_id)}-{uuid.uuid4().hex[:8]}"
        root = PROFILES / user_id / "models" / version
        state = {"status": "training", "approved": False, "version": version,
                 "stage": "Preparando dataset", "epochs": epochs,
                 "training_mode": mode, "epoch": 0, "batch_size": batch_size,
                 "sample_manifest": {item["id"]: item["sha256"] for item in dataset["samples"] if item["status"] == "accepted"},
                 "dataset_sha256": dataset_fingerprint(user_id), "log": str(root / "training.log")}
        if base:
            state.update(base_model=base, parent_version=base["version"], initialization=source,
                         base_sample_ids=sorted(trained_sample_ids(user_id, base)),
                         learning_rate=FINE_TUNE_LEARNING_RATE)
        save_model(user_id, state)
        cancelled.discard(user_id)
        trainer.submit(train_model, user_id, state, dataset["samples"])
        return state


def train_model(user_id, state, samples):
    root = Path(state["log"]).parent
    dataset = root / "dataset"
    holdout = root / "holdout"
    try:
        dataset.mkdir(parents=True, exist_ok=True)
        holdout.mkdir(exist_ok=True)
        accepted = [item for item in samples if item["status"] == "accepted"]
        # Hold out complete takes, never overlapping slices of a training take.
        base = state.get("base_model", {})
        reserved = set(base.get("holdout_ids", []))
        base_ids = set(state.get("base_sample_ids", []))
        rng = random.Random(42)
        for language in ("es", "en"):
            for mode in ("speak", "sing"):
                group = [item for item in accepted if item["language"] == language and item["mode"] == mode and item["id"] not in base_ids]
                rng.shuffle(group)
                reserved.update(item["id"] for item in group[:max(1, len(group) // 10)] if len(group) > 2)
        for item in accepted:
            source = directory(user_id) / item["filename"]
            if fingerprint(source) != item["sha256"]:
                raise ValueError("Una toma cambio desde su revision; revisala de nuevo.")
            target = holdout if item["id"] in reserved else dataset
            sf.write(target / f"{item['id']}.wav", decode(source, 40000), 40000, subtype="PCM_16")
        state.update(holdout_ids=sorted(reserved), stage="Segmentando audio limpio")
        save_model(user_id, state)
        experiment = APPLIO / "logs" / state["version"]
        log = state["log"]
        checked_command(["rvc/train/preprocess/preprocess.py", experiment, dataset,
                         40000, 4, "Automatic", "False", "False", 0.0, 3.0, 0.3, "post"], log, user_id)
        state["stage"] = "Extrayendo contenido y tono real"
        save_model(user_id, state)
        import torch
        extraction_device = "0" if torch.cuda.is_available() else "-"
        checked_command(["rvc/train/extract/extract.py", experiment, "rmvpe", 2, extraction_device,
                         40000, "contentvec", "None", 0], log, user_id)
        filelist = (experiment / "filelist.txt").read_text().splitlines()
        slice_count = len(list((experiment / "sliced_audios").glob("*.wav")))
        if len(filelist) < 20 or len(filelist) < slice_count * 0.9:
            raise ValueError("La extraccion produjo muy pocos segmentos validos.")
        # Upstream extraction can swallow worker failures. Validate every artifact.
        for line in filelist:
            if not all((APPLIO / name).is_file() for name in line.split("|")[:4]):
                raise ValueError("Faltan caracteristicas o etiquetas F0 en el dataset.")
        state["stage"] = "Ajustando tu modelo personal (fine-tuning)" if base else "Entrenando pesos personales RVC"
        save_model(user_id, state)
        pretrained = APPLIO / "rvc/models/pretraineds/hifi-gan"
        generator, discriminator = pretrained / "f0G40k.pth", pretrained / "f0D40k.pth"
        if base:
            initialization = state["initialization"]
            for key in ("generator", "discriminator", "config"):
                if fingerprint(initialization[key]) != initialization[key + "_sha256"]:
                    raise ValueError("Los checkpoints de origen cambiaron durante la preparacion.")
            config = json.loads(Path(initialization["config"]).read_text())
            config.pop("process_pids", None)
            config["train"]["learning_rate"] = min(FINE_TUNE_LEARNING_RATE, config["train"]["learning_rate"])
            config["model"]["spk_embed_dim"] = initialization["speakers"]
            (experiment / "config.json").write_text(json.dumps(config, indent=2))
            state["learning_rate"] = config["train"]["learning_rate"]
            generator, discriminator = initialization["generator"], initialization["discriminator"]
        arguments = ["rvc/train/train.py", state["version"], 10 if base else 25, state["epochs"],
                     generator, discriminator, "0", state.get("batch_size", 4), 40000, "True", "False", "False", "False", "HiFi-GAN", "False"]
        checked_command(arguments + (["fine-tune"] if base else []), log, user_id)
        import torch
        candidates = []
        for path in experiment.glob(f"{state['version']}_*.pth"):
            checkpoint = torch.load(path, map_location="cpu", weights_only=False)
            if {"weight", "config", "epoch"} <= checkpoint.keys():
                candidates.append((checkpoint["epoch"], path))
        if not candidates or max(candidates)[0] < state["epochs"]:
            raise ValueError("El entrenamiento no genero el checkpoint final esperado.")
        final = max(candidates)[1]
        checked_command(["rvc/train/process/extract_index.py", experiment, "Faiss"], log, user_id)
        index = experiment / f"{state['version']}.index"
        if not index.is_file() or index.stat().st_size < 1000:
            raise ValueError("No se genero el indice de recuperacion personal.")
        shutil.copy2(final, root / "voice.pth")
        shutil.copy2(index, root / "voice.index")
        state.update(status="trained", stage="Modelo entrenado; prueba pendiente",
                     checkpoint=str(root / "voice.pth"), index=str(root / "voice.index"),
                     checkpoint_sha256=fingerprint(root / "voice.pth"), epoch=state["epochs"],
                     engine="RVC v2 personal / RMVPE / ContentVec / 40 kHz", error=None)
        paths = training_checkpoints(state)
        state["training_checkpoints"] = {**{key: str(path) for key, path in paths.items()},
                                         **{key + "_sha256": fingerprint(path) for key, path in paths.items()}}
    except Exception as error:
        state.update(status="cancelled" if user_id in cancelled else "failed",
                     stage="Entrenamiento cancelado" if user_id in cancelled else "Error de entrenamiento",
                     error=str(error))
    with model_lock:
        save_model(user_id, state)
        if state["status"] in {"failed", "cancelled"} and state.get("base_model"):
            restore_after_failure(user_id, state)


def restore_after_failure(user_id, state):
    restored = dict(state["base_model"])
    restored["last_training"] = {key: state.get(key) for key in ("version", "status", "stage", "error", "log")}
    save_model(user_id, restored)


def restore_previous_model(user_id):
    with model_lock:
        state = model_status(user_id)
        if state["status"] == "training" or state.get("preview", {}).get("status") == "processing":
            raise ValueError("Espera a que termine el entrenamiento o la prueba.")
        if not state.get("base_model"):
            raise ValueError("No hay un modelo anterior asociado a esta actualizacion.")
        restored = validate_model_files(state["base_model"])
        save_model(user_id, restored)
        return model_status(user_id)


def approve_model(user_id):
    with model_lock:
        state = require_model(user_id, approved=False)
        if state.get("preview", {}).get("status") != "completed":
            raise ValueError("Genera y escucha una prueba antes de aprobar el modelo.")
        preview = state["preview"]
        if not all(Path(preview.get(key, "")).is_file() for key in ("output", "original")):
            raise ValueError("No se encuentran los audios de la prueba.")
        state.update(approved=True, stage="Modelo aprobado para conversion")
        save_model(user_id, state)
        return state


def cancel_training(user_id):
    with model_lock:
        state = model_status(user_id)
        if state["status"] != "training":
            raise ValueError("No hay un entrenamiento activo.")
        cancelled.add(user_id)
        process = active_processes.get(user_id)
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        state["stage"] = "Cancelando entrenamiento"
        save_model(user_id, state)
        return state


def recover_training():
    for path in PROFILES.glob("*/personal_model.json"):
        state = json.loads(path.read_text())
        if state["status"] == "training":
            state.update(status="failed", error="El entrenamiento se interrumpio; vuelve a iniciarlo.")
            save_model(path.parent.name, state)
            if state.get("base_model"):
                restore_after_failure(path.parent.name, state)
                continue
        if state.get("preview", {}).get("status") == "processing":
            state["preview"] = {"status": "failed", "error": "La prueba se interrumpio."}
        save_model(path.parent.name, state)
