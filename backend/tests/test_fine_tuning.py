import json
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from fastapi.testclient import TestClient

from app.services import personal_model, previews, samples
from app.services.audio import fingerprint


@pytest.fixture
def personal_source(tmp_path, monkeypatch):
    applio = tmp_path / "Applio"
    monkeypatch.setattr(personal_model, "APPLIO", applio)
    (applio / "assets").mkdir(parents=True)
    (applio / "assets/config.json").write_text("{}")
    experiment = applio / "logs/user-old"
    experiment.mkdir(parents=True)
    root = personal_model.PROFILES / "user/models/user-old"
    root.mkdir(parents=True)
    weights = {"emb_g.weight": torch.tensor([[0.25, 0.75]]), "decoder.weight": torch.ones(2, 2)}
    torch.save({"model": weights, "iteration": 200}, experiment / "G_2333333.pth")
    torch.save({"model": {"weight": torch.ones(2, 2)}, "iteration": 200}, experiment / "D_2333333.pth")
    (experiment / "config.json").write_text(json.dumps({"train": {"learning_rate": 0.0001},
        "data": {"sample_rate": 40000}, "model": {"spk_embed_dim": 109}, "process_pids": [123]}))
    torch.save({"weight": {key: value.half() for key, value in weights.items()}, "epoch": 200,
                "sr": 40000, "f0": True, "version": "v2", "vocoder": "HiFi-GAN"}, root / "voice.pth")
    (root / "voice.index").write_bytes(b"personal index")
    takes = []
    for i in range(7):
        item = {"id": f"take{i}", "filename": f"take{i}.wav", "status": "accepted",
                "prompt_id": f"es-{i}", "language": "es", "mode": "speak" if i < 4 else "sing",
                "quality": {"active_seconds": 90}, "alignment": {"coverage": 1}}
        directory = samples.directory("user")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / item["filename"]).write_bytes(f"recording {i}".encode())
        item["sha256"] = fingerprint(directory / item["filename"])
        samples.save_sample("user", item)
        takes.append(item)
        if i == 3:
            base = {"status": "trained", "approved": True, "version": "user-old", "epoch": 199,
                    "epochs": 200, "checkpoint": str(root / "voice.pth"), "index": str(root / "voice.index"),
                    "checkpoint_sha256": fingerprint(root / "voice.pth"), "engine": "personal RVC",
                    "dataset_sha256": samples.dataset_fingerprint("user"), "holdout_ids": ["take3"],
                    "sample_manifest": {take["id"]: take["sha256"] for take in takes}}
            personal_model.save_model("user", base)
    monkeypatch.setattr(personal_model, "dataset_status", lambda user: {"ready": True, "missing": [], "samples": takes})
    submitted = []
    monkeypatch.setattr(personal_model, "trainer", SimpleNamespace(submit=lambda *args: submitted.append(args)))
    return SimpleNamespace(base=base, takes=takes, experiment=experiment, root=root, submitted=submitted)


def test_fine_tuning_starts_from_validated_personal_checkpoints_without_generic_pretrained(personal_source):
    source = personal_source
    original = (source.root / "voice.pth").read_bytes()
    state = personal_model.start_training("user", mode="fine_tune")
    assert state["epochs"] == 50
    assert state["training_mode"] == "fine_tune"
    assert state["version"] != source.base["version"]
    assert state["parent_version"] == source.base["version"]
    assert state["base_model"]["approved"]  # Preserve persisted approval, not the derived stale flag.
    assert state["initialization"]["generator"] == str(source.experiment / "G_2333333.pth")
    assert state["initialization"]["epoch"] == 200
    assert set(state["sample_manifest"]) == {take["id"] for take in source.takes}
    assert set(state["base_sample_ids"]) == {"take0", "take1", "take2", "take3"}
    assert source.submitted[0][0] is personal_model.train_model
    assert (source.root / "voice.pth").read_bytes() == original
    assert json.loads((source.root / "model.json").read_text())["approved"]


@pytest.mark.parametrize("problem", ["no_changes", "missing_discriminator", "mismatched_epoch", "wrong_weights"])
def test_invalid_fine_tuning_never_replaces_active_model_or_falls_back(personal_source, problem):
    source = personal_source
    if problem == "no_changes":
        state = {**source.base, "dataset_sha256": samples.dataset_fingerprint("user")}
        personal_model.save_model("user", state)
    elif problem == "missing_discriminator":
        (source.experiment / "D_2333333.pth").unlink()
    elif problem == "mismatched_epoch":
        torch.save({"model": {}, "iteration": 199}, source.experiment / "D_2333333.pth")
    else:
        torch.save({"model": {"emb_g.weight": torch.zeros(1, 2)}, "iteration": 200}, source.experiment / "G_2333333.pth")
    before = personal_model.state_path("user").read_bytes()
    with pytest.raises(ValueError):
        personal_model.start_training("user", mode="fine_tune")
    assert personal_model.state_path("user").read_bytes() == before
    assert not source.submitted


def test_stored_training_checkpoint_hashes_are_enforced(personal_source):
    state = personal_source.base
    state["training_checkpoints"] = personal_model.validate_training_source(state)
    state["training_checkpoints"]["discriminator_sha256"] = "changed"
    with pytest.raises(ValueError, match="cambio"):
        personal_model.validate_training_source(state)


def test_fit_reuses_old_and_new_takes_stable_holdouts_lower_lr_and_personal_pair(personal_source, monkeypatch):
    source = personal_source
    state = personal_model.start_training("user", epochs=17, mode="fine_tune")
    commands = []
    monkeypatch.setattr(personal_model, "decode", lambda *args: np.ones(400, dtype=np.float32) * 0.1)

    def command(args, *ignored):
        commands.append(args)
        experiment = personal_model.APPLIO / "logs" / state["version"]
        experiment.mkdir(parents=True, exist_ok=True)
        if "extract/extract.py" in str(args[0]):
            artifact = experiment / "feature.npy"
            artifact.write_bytes(b"feature")
            (experiment / "filelist.txt").write_text(("|".join([str(artifact)] * 4) + "|0\n") * 20)
        elif args[0] == "rvc/train/train.py":
            assert args[1:4] == [state["version"], 10, 17]
            assert args[4:6] == [str(source.experiment / "G_2333333.pth"), str(source.experiment / "D_2333333.pth")]
            assert args[-1] == "fine-tune"
            config = json.loads((experiment / "config.json").read_text())
            assert config["train"]["learning_rate"] == 0.00002
            assert config["model"]["spk_embed_dim"] == 1
            assert "process_pids" not in config
            for prefix in ("G", "D"):
                torch.save({"iteration": 17, "model": {}}, experiment / f"{prefix}_2333333.pth")
            torch.save({"weight": {}, "config": [], "epoch": 17}, experiment / f"{state['version']}_17e_1s.pth")
        elif "extract_index.py" in str(args[0]):
            (experiment / f"{state['version']}.index").write_bytes(b"i" * 2000)

    monkeypatch.setattr(personal_model, "checked_command", command)
    personal_model.train_model("user", state, source.takes)
    assert state["status"] == "trained", state.get("error")
    assert state["epoch"] == 17
    root = personal_model.state_path("user").parent / "models" / state["version"]
    trained = {path.stem for path in (root / "dataset").glob("*.wav")}
    held = {path.stem for path in (root / "holdout").glob("*.wav")}
    assert {"take0", "take1", "take2"} <= trained
    assert "take3" in held
    assert len(held & {"take4", "take5", "take6"}) == 1
    assert trained | held == {take["id"] for take in source.takes}
    assert not (trained & held)
    assert state["training_checkpoints"]["generator_sha256"]
    assert source.base["checkpoint_sha256"] == fingerprint(source.base["checkpoint"])


@pytest.mark.parametrize("cancel", [False, True])
def test_failed_or_cancelled_fit_restores_previous_model_and_keeps_failure_record(personal_source, monkeypatch, cancel):
    state = personal_model.start_training("user", mode="fine_tune")
    if cancel:
        personal_model.cancelled.add("user")
    monkeypatch.setattr(personal_model, "decode", lambda *args: (_ for _ in ()).throw(RuntimeError("test failure")))
    try:
        personal_model.train_model("user", state, personal_source.takes)
    finally:
        personal_model.cancelled.discard("user")
    restored = json.loads(personal_model.state_path("user").read_text())
    assert restored["version"] == "user-old"
    assert restored["approved"]
    assert restored["last_training"]["status"] == ("cancelled" if cancel else "failed")
    assert restored["last_training"]["error"] == "test failure"
    failed = json.loads((personal_model.PROFILES / "user/models" / state["version"] / "model.json").read_text())
    assert failed["status"] == restored["last_training"]["status"]


def test_restart_recovers_base_model_and_manual_restore_validates_its_files(personal_source):
    state = personal_model.start_training("user", mode="fine_tune")
    personal_model.recover_training()
    recovered = personal_model.model_status("user")
    assert recovered["version"] == "user-old"
    assert recovered["last_training"]["status"] == "failed"
    assert recovered["stale"]
    assert recovered["epoch"] == 200
    state["status"] = "trained"
    personal_model.save_model("user", state)
    assert personal_model.restore_previous_model("user")["version"] == "user-old"
    personal_model.save_model("user", state)
    (personal_source.root / "voice.pth").write_bytes(b"modified")
    with pytest.raises(ValueError, match="checkpoint"):
        personal_model.restore_previous_model("user")
    assert personal_model.model_status("user")["version"] == state["version"]


def test_training_api_freezes_reviews_and_delivers_previous_preview_only_when_present(personal_source):
    from app.main import app
    with TestClient(app) as client:
        assert client.post("/api/studio/user/train", json={"mode": "fine_tune", "epochs": 9}).status_code == 422
        assert client.post("/api/studio/user/train", json={"mode": "fine_tune", "epochs": 201}).status_code == 409
        response = client.post("/api/studio/user/train", json={"mode": "fine_tune"})
        assert response.status_code == 200
        assert response.json()["epochs"] == 50
        assert client.post("/api/studio/user/samples/take0/review", json={"accept": False}).status_code == 409
        assert samples.read_sample("user", "take0")["status"] == "accepted"
        assert client.post("/api/studio/user/train/restore").status_code == 409
        state = response.json()
        state.update(status="trained", preview={"status": "completed", "id": "proof"})
        personal_model.save_model("user", state)
        assert client.get("/api/studio/user/preview/previous").status_code == 404
        previous = personal_source.root / "previous.mp3"
        previous.write_bytes(b"previous voice")
        state["preview"]["previous_output"] = str(previous)
        personal_model.save_model("user", state)
        assert client.get("/api/studio/user/preview/previous").content == b"previous voice"
        assert personal_model.model_status("user")["preview"]["previous_url"].endswith("previous?v=proof")
        assert client.post("/api/studio/user/train/restore").json()["version"] == "user-old"


def test_paired_preview_uses_same_vocal_source_and_pitch_for_both_models(personal_source, monkeypatch):
    state = {"status": "trained", "version": "user-new", "base_model": personal_source.base,
             "preview": {"status": "processing", "id": "comparison", "pitch_shift": -12}}
    monkeypatch.setattr(previews, "decode", lambda *args: np.ones(15 * 44100, dtype=np.float32) * 0.1)
    monkeypatch.setattr(previews, "separate_song", lambda *args: {"vocals": "same vocal", "instrumental": "same music"})
    calls = []
    def current(source, user, pitch, output, approved):
        assert not approved
        calls.append((source, pitch, "user-new"))
        return {"model_version": "user-new"}
    def previous(source, model, pitch, output):
        calls.append((source, pitch, model["version"]))
        return {"model_version": model["version"]}
    monkeypatch.setattr(previews, "convert_personal", current)
    monkeypatch.setattr(previews, "convert_snapshot_preview", previous)
    monkeypatch.setattr(previews, "mix_final_song", lambda *args: args[-1].write_bytes(b"preview"))
    previews.run_preview("user", "song", 0, state)
    assert calls == [("same vocal", -12, "user-new"), ("same vocal", -12, "user-old")]
    assert state["preview"]["status"] == "completed"
    assert state["preview"]["previous_metrics"]["model_version"] == "user-old"


def test_engine_loader_preserves_speaker_and_all_weights_with_fresh_optimizer(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(personal_model.BACKEND / "Applio"))
    from rvc.train.finetuning import load_fine_tune_weights
    from rvc.train.utils import replace_keys_in_dict
    class Generator(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.emb_g = torch.nn.Embedding(1, 2)
            self.decoder = torch.nn.utils.parametrizations.weight_norm(torch.nn.Linear(2, 2))
    generator, discriminator = Generator(), torch.nn.Linear(2, 1)
    with torch.no_grad():
        generator.emb_g.weight.fill_(0.42)
    expected = {key: value.clone() for key, value in generator.state_dict().items()}
    legacy = replace_keys_in_dict(expected, ".parametrizations.weight.original1", ".weight_v")
    legacy = replace_keys_in_dict(legacy, ".parametrizations.weight.original0", ".weight_g")
    g, d = tmp_path / "G.pth", tmp_path / "D.pth"
    torch.save({"model": legacy, "iteration": 200, "optimizer": "must not load"}, g)
    torch.save({"model": discriminator.state_dict(), "iteration": 200}, d)
    target = Generator()
    optimizer = torch.optim.AdamW(target.parameters(), lr=0.00002)
    assert load_fine_tune_weights(target, discriminator, g, d) == 200
    assert all(torch.equal(value, target.state_dict()[key]) for key, value in expected.items())
    assert not optimizer.state
    assert optimizer.param_groups[0]["lr"] == 0.00002
    torch.save({"model": discriminator.state_dict(), "iteration": 199}, d)
    with pytest.raises(ValueError, match="same epoch"):
        load_fine_tune_weights(target, discriminator, g, d)
    torch.save({"model": {}, "iteration": 200}, d)
    with pytest.raises(RuntimeError, match="Missing key"):
        load_fine_tune_weights(target, discriminator, g, d)
