"""Portable, checksummed bundles; inference exports never contain recordings."""
import json
import shutil
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from app.config import AUDIO_EXTENSIONS, safe_id
from app.services import personal_model as models, samples
from app.services.audio import fingerprint

MODEL_LIMIT = 512 * 1024 * 1024
TRAINING_LIMIT = 4 * 1024 * 1024 * 1024
CONFIG_40K = [1025, 32, 192, 192, 768, 2, 6, 3, 0, "1", [3, 7, 11],
              [[1, 3, 5], [1, 3, 5], [1, 3, 5]], [10, 10, 2, 2], 512, [16, 16, 4, 4], 1, 256, 40000]


def validate_compact(checkpoint, index):
    import torch
    import faiss
    data = torch.load(checkpoint, weights_only=True, mmap=True, map_location="cpu")
    if (data.get("config") != CONFIG_40K or data.get("version") != "v2" or data.get("sr") != 40000
            or not data.get("f0") or data.get("vocoder") != "HiFi-GAN"
            or data.get("embedder_model", "contentvec") != "contentvec"):
        raise ValueError("Solo se admiten modelos personales RVC v2 / 40 kHz / ContentVec / HiFi-GAN de un hablante.")
    weights = data.get("weight", {})
    if not isinstance(weights, dict) or not 100 <= len(weights) <= 1000:
        raise ValueError("Checkpoint personal incompleto.")
    for key, weight in weights.items():
        if not isinstance(key, str) or not isinstance(weight, torch.Tensor) or weight.numel() > 50000000 or not torch.isfinite(weight).all():
            raise ValueError("Pesos del modelo no validos.")
    import sys
    sys.path.insert(0, str(models.APPLIO))
    from rvc.lib.algorithm.synthesizers import Synthesizer
    network = Synthesizer(*CONFIG_40K, use_f0=True, vocoder="HiFi-GAN")
    expected = {key: value for key, value in network.state_dict().items() if not key.startswith("enc_q.")}
    from rvc.train.utils import replace_keys_in_dict
    normalized = replace_keys_in_dict(weights, ".weight_v", ".parametrizations.weight.original1")
    normalized = replace_keys_in_dict(normalized, ".weight_g", ".parametrizations.weight.original0")
    if set(normalized) != set(expected) or any(normalized[key].shape != value.shape for key, value in expected.items()):
        raise ValueError("Los pesos no corresponden a la arquitectura personal RVC.")
    retrieval = faiss.read_index(str(index))
    if retrieval.d != 768 or retrieval.ntotal < 1:
        raise ValueError("Indice ContentVec no valido.")
    # Keep only supported primitive metadata and tensors, never arbitrary pickle objects.
    return {"weight": weights, "config": CONFIG_40K, "epoch": int(data.get("epoch", 0)),
            "sr": 40000, "f0": 1, "version": "v2", "vocoder": "HiFi-GAN", "embedder_model": "contentvec"}


def portable_metadata(user_id, state):
    keys = ("version", "epochs", "epoch", "training_mode", "dataset_sha256", "holdout_ids", "sample_manifest")
    data = {key: state[key] for key in keys if key in state}
    data.setdefault("sample_manifest", {identifier: "legacy" for identifier in models.trained_sample_ids(user_id, state)})
    return data


def export_bundle(user_id, kind="model", destination=None):
    if kind not in {"model", "training"}:
        raise ValueError("Tipo de paquete no valido.")
    with models.model_lock:
        state = models.model_status(user_id)
        if state["status"] == "training" or state.get("preview", {}).get("status") == "processing":
            raise ValueError("Espera a que termine el entrenamiento o la prueba.")
        files = {}
        manifest = {"format": "your-voice-your-song", "schema": 1, "kind": kind}
        if state["status"] == "trained":
            state = models.validate_model_files(state)
            prefix = "" if kind == "model" else "base/"
            files.update({prefix + "voice.pth": Path(state["checkpoint"]), prefix + "voice.index": Path(state["index"])})
            manifest["model"] = portable_metadata(user_id, state)
            if kind == "model":
                manifest["model"].pop("sample_manifest", None)
                manifest["model"].pop("holdout_ids", None)
            else:
                if state.get("inference_only"):
                    raise ValueError("Este modelo solo contiene pesos de inferencia; faltan G/D para entrenar.")
                source = models.validate_training_source(state)
                files.update({f"base/training/{name}": Path(source[key]) for key, name in
                    (("generator", "G.pth"), ("discriminator", "D.pth"), ("config", "config.json"))})
        elif kind == "model":
            raise ValueError("No hay un modelo entrenado para exportar.")
        if kind == "training":
            dataset = samples.dataset_status(user_id)
            if not dataset["ready"]:
                raise ValueError(" ".join(dataset["missing"]))
            manifest["samples"] = []
            for item in dataset["samples"]:
                if item["status"] != "accepted":
                    continue
                filename = safe_id(item["id"]) + Path(item["filename"]).suffix.lower()
                source = samples.directory(user_id) / item["filename"]
                if source.suffix.lower() not in AUDIO_EXTENSIONS or fingerprint(source) != item["sha256"]:
                    raise ValueError("Una toma cambio desde su revision.")
                files["samples/" + filename] = source
                manifest["samples"].append({key: item[key] for key in
                    ("id", "prompt_id", "language", "mode", "sha256", "quality")})
                manifest["samples"][-1].update(filename=filename, status="accepted", issues=[], alignment={"coverage": 1, "words": [], "missing_words": []})
        limit = MODEL_LIMIT if kind == "model" else TRAINING_LIMIT
        if sum(path.stat().st_size for path in files.values()) > limit:
            raise ValueError("El paquete supera el limite de tamaño.")
        if destination is None:
            exports = models.PROFILES.parent / "exports"
            exports.mkdir(parents=True, exist_ok=True)
            destination = exports / f"{kind}-{uuid.uuid4().hex}.zip"
        destination = Path(destination)
        if destination.suffix.lower() != ".zip" or any(destination.resolve() == path.resolve() for path in files.values()):
            raise ValueError("Exporta a un ZIP separado, nunca sobre los archivos del modelo o las muestras.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        manifest["files"] = {name: {"bytes": path.stat().st_size, "sha256": fingerprint(path)} for name, path in files.items()}
        try:
            with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
                for name, path in files.items():
                    archive.write(path, name)
                archive.writestr("manifest.json", json.dumps(manifest))
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return destination


def unpack_bundle(source, root, kind):
    limit = MODEL_LIMIT if kind == "model" else TRAINING_LIMIT
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(entries) > 2000 or len(names) != len(set(names)) or sum(entry.file_size for entry in entries) > limit:
            raise ValueError("ZIP duplicado, demasiado grande o con demasiados archivos.")
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in entry.filename or path.as_posix() != entry.filename or entry.is_dir() or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
                raise ValueError("Ruta o entrada ZIP no valida.")
        info = archive.getinfo("manifest.json")
        if info.file_size > 1024 * 1024:
            raise ValueError("Manifest demasiado grande.")
        manifest = json.loads(archive.read(info))
        if manifest.get("format") != "your-voice-your-song" or manifest.get("schema") != 1 or manifest.get("kind") != kind:
            raise ValueError("El ZIP no es un paquete compatible de este proyecto.")
        declared = manifest.get("files", {})
        if set(names) != set(declared) | {"manifest.json"}:
            raise ValueError("Los archivos no coinciden con el manifest.")
        allowed = {"voice.pth", "voice.index"} if kind == "model" else {
            "base/voice.pth", "base/voice.index", "base/training/G.pth", "base/training/D.pth", "base/training/config.json"}
        for name, metadata in declared.items():
            if name not in allowed and not (kind == "training" and name.startswith("samples/") and len(PurePosixPath(name).parts) == 2 and Path(name).suffix.lower() in AUDIO_EXTENSIONS):
                raise ValueError("El paquete contiene archivos no admitidos.")
            if archive.getinfo(name).file_size != metadata["bytes"]:
                raise ValueError("Tamaño incorrecto en el manifest.")
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as reader, target.open("wb") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
            if fingerprint(target) != metadata["sha256"]:
                raise ValueError("Checksum incorrecto; el paquete esta corrupto.")
        if kind == "model" and set(declared) != allowed:
            raise ValueError("Faltan los pesos o el indice personal.")
        return manifest


def imported_state(root, metadata, inference_only):
    return {"status": "trained", "approved": False, "version": root.name,
            "checkpoint": str(root / "voice.pth"), "index": str(root / "voice.index"),
            "checkpoint_sha256": fingerprint(root / "voice.pth"), "inference_only": inference_only,
            "engine": "RVC v2 personal / RMVPE / ContentVec / 40 kHz",
            "stage": "Modelo importado; prueba pendiente", **{key: metadata[key] for key in
                ("epoch", "epochs", "dataset_sha256", "holdout_ids", "sample_manifest") if key in metadata}}


def import_model(user_id, source):
    import torch
    with models.model_lock:
        current = models.model_status(user_id)
        if current["status"] == "training" or current.get("preview", {}).get("status") == "processing":
            raise ValueError("Espera a que termine el proceso actual antes de importar.")
        parent = models.PROFILES / safe_id(user_id) / "models"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".import-", dir=parent) as temporary:
            staging = Path(temporary)
            manifest = unpack_bundle(source, staging, "model")
            canonical = validate_compact(staging / "voice.pth", staging / "voice.index")
            torch.save(canonical, staging / "voice.pth")
            root = parent / f"{safe_id(user_id)}-import-{uuid.uuid4().hex[:8]}"
            staging.rename(root)
            state = imported_state(root, manifest.get("model", {}), True)
            if current["status"] == "trained":
                state.update(base_model=models.model_snapshot(json.loads(models.state_path(user_id).read_text())), parent_version=current["version"])
            models.save_model(user_id, state)
            return models.model_status(user_id)


def import_training(user_id, source):
    root = models.PROFILES / safe_id(user_id)
    if root.exists():
        raise ValueError("Importa entrenamiento en un perfil nuevo; nunca sobrescribas tus grabaciones.")
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".training-", dir=root.parent) as temporary:
        staging = Path(temporary)
        payload = staging / "payload"
        payload.mkdir()
        manifest = unpack_bundle(source, payload, "training")
        accepted = manifest.get("samples", [])
        if not accepted or len({item["id"] for item in accepted}) != len(accepted):
            raise ValueError("Dataset de entrenamiento vacio o duplicado.")
        sample_root = staging / "samples"
        sample_root.mkdir()
        for item in accepted:
            identifier = safe_id(item["id"])
            filename = identifier + Path(item["filename"]).suffix.lower()
            if filename != item["filename"] or item["language"] not in {"es", "en"} or item["mode"] not in {"speak", "sing"} or item.get("status") != "accepted":
                raise ValueError("Metadata de muestra no valida.")
            active = item.get("quality", {}).get("active_seconds")
            if not isinstance(active, (int, float)) or not 0 < active <= 600 or not isinstance(item.get("prompt_id"), str):
                raise ValueError("Calidad o texto de muestra no valido.")
            recording = payload / "samples" / filename
            if fingerprint(recording) != item["sha256"]:
                raise ValueError("La muestra no coincide con su checksum.")
            shutil.move(recording, sample_root / filename)
            (sample_root / f"{identifier}.json").write_text(json.dumps(item))
        if "model" in manifest:
            base = staging / "models" / safe_id(manifest["model"]["version"])
            base.parent.mkdir()
            shutil.move(payload / "base", base)
            validate_compact(base / "voice.pth", base / "voice.index")
            provisional = imported_state(base, manifest["model"], False)
            provisional["training_checkpoints"] = {key: str(base / "training" / name) for key, name in
                (("generator", "G.pth"), ("discriminator", "D.pth"), ("config", "config.json"))}
            models.validate_training_source(provisional)
        shutil.rmtree(payload)
        staging.rename(root)
    if "model" in manifest:
        base = root / "models" / manifest["model"]["version"]
        state = imported_state(base, manifest["model"], False)
        paths = {key: str(base / "training" / name) for key, name in
            (("generator", "G.pth"), ("discriminator", "D.pth"), ("config", "config.json"))}
        state["training_checkpoints"] = {**paths, **{key + "_sha256": fingerprint(path) for key, path in paths.items()}}
        models.validate_training_source(state)
        models.save_model(user_id, state)
    return samples.dataset_status(user_id)
