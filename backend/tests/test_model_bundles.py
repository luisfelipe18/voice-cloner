import io
import json
import stat
import zipfile

import pytest
import torch
from fastapi.testclient import TestClient

from app.services import model_bundles as bundles, personal_model as models, samples, previews
from app.services.audio import fingerprint


@pytest.fixture
def portable_voice(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "APPLIO", tmp_path / "Applio")
    experiment = models.APPLIO / "logs/voice-old"
    experiment.mkdir(parents=True)
    (models.APPLIO / "assets").mkdir()
    (models.APPLIO / "assets/config.json").write_text("{}")
    root = models.PROFILES / "user/models/voice-old"
    root.mkdir(parents=True)
    weights = {"emb_g.weight": torch.ones(1, 256), "decoder.weight": torch.ones(2, 2)}
    torch.save({"weight": weights, "config": bundles.CONFIG_40K, "epoch": 200,
                "sr": 40000, "version": "v2", "vocoder": "HiFi-GAN", "f0": 1}, root / "voice.pth")
    (root / "voice.index").write_bytes(b"own index")
    torch.save({"model": weights, "iteration": 200}, experiment / "G_2333333.pth")
    torch.save({"model": {}, "iteration": 200}, experiment / "D_2333333.pth")
    (experiment / "config.json").write_text(json.dumps({"data": {"sample_rate": 40000}}))
    for i in range(12):
        item = {"id": f"take{i}", "filename": f"take{i}.wav", "status": "accepted", "language": "es" if i < 6 else "en",
                "mode": "sing" if i % 2 == 0 else "speak", "prompt_id": f"prompt{i}",
                "quality": {"active_seconds": 60, "duration": 65}, "transcript": "do not export transcripts",
                "guide": {"name": "do not export songs"}, "expected_text": "private lyrics"}
        path = samples.directory("user") / item["filename"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"own voice {i}".encode())
        item["sha256"] = fingerprint(path)
        samples.save_sample("user", item)
    state = {"version": "voice-old", "status": "trained", "approved": True, "epochs": 200, "epoch": 200,
             "checkpoint": str(root / "voice.pth"), "index": str(root / "voice.index"),
             "checkpoint_sha256": fingerprint(root / "voice.pth"), "dataset_sha256": samples.dataset_fingerprint("user"),
             "engine": "RVC personal", "holdout_ids": ["take3"],
             "sample_manifest": {item["id"]: item["sha256"] for item in samples.list_samples("user")}}
    models.save_model("user", state)
    monkeypatch.setattr(bundles, "validate_compact", lambda checkpoint, index: torch.load(checkpoint, weights_only=True, mmap=True))
    return state


def test_inference_export_contains_only_model_index_and_manifest_no_private_paths(portable_voice, tmp_path):
    path = bundles.export_bundle("user", "model", tmp_path / "model.zip")
    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist()) == {"voice.pth", "voice.index", "manifest.json"}
        metadata = json.loads(archive.read("manifest.json"))
        assert "sample_manifest" not in metadata["model"]
        assert "holdout_ids" not in metadata["model"]
        assert "checkpoint" not in metadata["model"]
        assert metadata["files"]["voice.pth"]["sha256"] == portable_voice["checkpoint_sha256"]


def test_import_on_empty_inference_machine_requires_preview_not_training_data(portable_voice, tmp_path, monkeypatch):
    source = bundles.export_bundle("user", "model", tmp_path / "model.zip")
    imported = bundles.import_model("empty", source)
    assert imported["inference_only"]
    assert imported["status"] == "trained" and not imported["approved"]
    assert not imported.get("stale")
    assert not imported["fine_tune"]["available"]
    assert not samples.list_samples("empty")
    with pytest.raises(ValueError, match="prueba"):
        models.require_model("empty")
    assert models.require_model("empty", approved=False)["version"] == imported["version"]
    # Dataset requirements never block inference-only preview/approval.
    output = tmp_path / "preview.mp3"
    output.write_bytes(b"own audition")
    imported["preview"] = {"status": "completed", "id": "proof", "output": str(output), "original": str(output)}
    models.save_model("empty", imported)
    assert models.approve_model("empty")["approved"]
    assert models.require_model("empty")["inference_only"]


def test_import_preserves_recordings_and_previous_model_with_explicit_restore(portable_voice, tmp_path):
    source = bundles.export_bundle("user", "model", tmp_path / "model.zip")
    before = {path.name: fingerprint(path) for path in samples.directory("user").glob("*")}
    imported = bundles.import_model("user", source)
    assert imported["parent_version"] == portable_voice["version"]
    assert {path.name: fingerprint(path) for path in samples.directory("user").glob("*")} == before
    assert fingerprint(portable_voice["checkpoint"]) == portable_voice["checkpoint_sha256"]
    assert models.restore_previous_model("user")["version"] == portable_voice["version"]


def test_private_training_bundle_round_trip_relocates_checkpoints_without_lyrics_or_songs(portable_voice, tmp_path):
    source = bundles.export_bundle("user", "training", tmp_path / "private.zip")
    with zipfile.ZipFile(source) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert len(manifest["samples"]) == 12
        assert all("transcript" not in item and "guide" not in item and "expected_text" not in item for item in manifest["samples"])
        assert all(name.startswith(("base/", "samples/")) or name == "manifest.json" for name in archive.namelist())
    result = bundles.import_training("colab", source)
    assert result["ready"]
    state = models.model_status("colab")
    assert not state["inference_only"]
    assert state["holdout_ids"] == ["take3"]
    assert state["dataset_sha256"] == samples.dataset_fingerprint("colab")
    initialized = models.validate_training_source(state)
    assert str(models.PROFILES / "colab") in initialized["generator"]
    with pytest.raises(ValueError, match="perfil nuevo"):
        bundles.import_training("colab", source)


@pytest.mark.parametrize("malformation", ["traversal", "duplicate", "symlink", "checksum", "unexpected", "oversize"])
def test_bad_archives_never_replace_active_model(portable_voice, tmp_path, malformation, monkeypatch):
    source = bundles.export_bundle("user", "model", tmp_path / "original.zip")
    with zipfile.ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    broken = tmp_path / "broken.zip"
    if malformation == "checksum":
        entries["voice.pth"] = b"corrupt"
    if malformation == "unexpected":
        entries["song.wav"] = b"must reject"
    if malformation == "oversize":
        monkeypatch.setattr(bundles, "MODEL_LIMIT", 10)
    with zipfile.ZipFile(broken, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        if malformation == "traversal":
            archive.writestr("../outside", b"no")
        if malformation == "duplicate":
            archive.writestr("voice.pth", b"duplicate")
        if malformation == "symlink":
            entry = zipfile.ZipInfo("link")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, b"voice.pth")
    before = models.state_path("user").read_bytes()
    with pytest.raises((ValueError, KeyError)):
        bundles.import_model("user", broken)
    assert models.state_path("user").read_bytes() == before
    assert not (tmp_path / "outside").exists()


def test_api_export_download_removes_temporary_zip_and_import_errors_leave_model(portable_voice):
    from app.main import app
    with TestClient(app) as client:
        response = client.post("/api/studio/user/bundles/export", json={"kind": "model"})
        assert response.status_code == 200
        url = response.json()["download_url"]
        download = client.get(url)
        assert download.status_code == 200
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            assert set(archive.namelist()) == {"manifest.json", "voice.pth", "voice.index"}
        assert client.get(url).status_code == 404
        result = client.post("/api/studio/clean/bundles/import", files={"bundle": ("model.zip", download.content, "application/zip")})
        assert result.status_code == 200
        assert result.json()["inference_only"]
        failed = client.post("/api/studio/clean/bundles/import", files={"bundle": ("bad.zip", b"not a zip")})
        assert failed.status_code == 400
        assert models.model_status("clean")["version"] == result.json()["version"]


def test_model_transfer_is_blocked_during_training_or_preview(portable_voice, tmp_path):
    source = bundles.export_bundle("user", "model", tmp_path / "model.zip")
    models.save_model("user", {**portable_voice, "status": "training"})
    with pytest.raises(ValueError, match="proceso actual"):
        bundles.import_model("user", source)
    with pytest.raises(ValueError, match="termine"):
        bundles.export_bundle("user")


def test_checkpoint_loader_rejects_executable_pickle_without_executing_it(tmp_path):
    class Executable:
        def __reduce__(self):
            return eval, ("__import__('os').environ.update({'UNSAFE_EXECUTED':'yes'})",)
    import os
    path = tmp_path / "voice.pth"
    torch.save(Executable(), path)
    with pytest.raises(Exception):
        bundles.validate_compact(path, tmp_path / "voice.index")
    assert "UNSAFE_EXECUTED" not in os.environ
