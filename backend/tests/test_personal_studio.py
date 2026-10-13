import io
import json

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app.services import samples, personal_model, rvc_converter
from app.services.audio import fingerprint
from app.services.prompts import catalog


@pytest.fixture
def profile_root(tmp_path, monkeypatch):
    monkeypatch.setattr(samples, "PROFILES", tmp_path)
    monkeypatch.setattr(personal_model, "PROFILES", tmp_path)
    return tmp_path


def test_prompts_cover_both_languages_without_duplicates():
    prompts = catalog()
    assert len(prompts) == 24
    assert len({prompt["id"] for prompt in prompts}) == 24
    assert sum(prompt["language"] == "es" for prompt in prompts) == 12
    assert sum(prompt["language"] == "en" for prompt in prompts) == 12


def test_word_comparison_keeps_real_timestamps_and_normalizes_accents():
    words = [{"text": "Abrí", "start": 1.2, "end": 1.5},
             {"text": "la", "start": 1.5, "end": 1.8},
             {"text": "ventana.", "start": 1.8, "end": 2.4}]
    result = samples.compare_words("Abri la ventana", words)
    assert result["coverage"] == 1
    assert result["words"][2]["end"] == 2.4
    result = samples.compare_words("Abrí la ventana azul", words)
    assert result["coverage"] == 0.75
    assert result["missing_words"] == ["azul"]


def test_silence_is_rejected_without_running_recognition(profile_root, monkeypatch):
    root = samples.directory("user")
    root.mkdir(parents=True)
    sf.write(root / "silent.wav", np.zeros(160000), 16000)
    samples.save_sample("user", {"id": "silent", "filename": "silent.wav",
                                "prompt_id": "es-01", "language": "es", "mode": "speak", "status": "queued"})
    def unexpected(*args):
        raise AssertionError("ASR must not run on silence")
    monkeypatch.setattr(samples, "transcribe", unexpected)
    samples.validate_sample("user", "silent")
    item = samples.read_sample("user", "silent")
    assert item["status"] == "rejected"
    assert item["quality"]["active_seconds"] == 0
    assert len(item["issues"]) >= 2


def test_bilingual_and_singing_requirements_cannot_be_bypassed(profile_root):
    for index in range(8):
        samples.save_sample("user", {"id": f"es{index}", "prompt_id": f"es-{index+1:02}",
                                    "language": "es", "mode": "speak", "status": "accepted",
                                    "sha256": str(index), "quality": {"active_seconds": 90}})
    state = samples.dataset_status("user")
    assert state["seconds"]["es"] == 720
    assert not state["ready"]
    with pytest.raises(ValueError, match="inglés"):
        personal_model.start_training("user")
    assert not personal_model.state_path("user").is_file()


def test_changing_accepted_dataset_invalidates_model_approval(profile_root):
    signature = samples.dataset_fingerprint("user")
    personal_model.save_model("user", {"status": "trained", "approved": True, "dataset_sha256": signature})
    assert personal_model.model_status("user")["approved"]
    samples.save_sample("user", {"id": "new", "status": "accepted", "sha256": "new"})
    state = personal_model.model_status("user")
    assert state["stale"]
    assert not state["approved"]
    with pytest.raises(ValueError, match="muestras actuales"):
        personal_model.require_model("user")


def test_approval_requires_a_generated_preview_and_unchanged_checkpoint(profile_root):
    root = profile_root / "user"
    root.mkdir()
    checkpoint, index = root / "voice.pth", root / "voice.index"
    checkpoint.write_bytes(b"diagnostic checkpoint")
    index.write_bytes(b"diagnostic index")
    personal_model.save_model("user", {"status": "trained", "approved": False,
        "checkpoint": str(checkpoint), "index": str(index), "checkpoint_sha256": fingerprint(checkpoint),
        "dataset_sha256": samples.dataset_fingerprint("user")})
    with pytest.raises(ValueError, match="prueba"):
        personal_model.approve_model("user")
    state = personal_model.model_status("user")
    state["preview"] = {"status": "completed", "output": str(checkpoint), "original": str(index)}
    personal_model.save_model("user", state)
    assert personal_model.approve_model("user")["approved"]
    checkpoint.write_bytes(b"changed")
    with pytest.raises(ValueError, match="checkpoint"):
        personal_model.require_model("user")


def test_no_rvc_output_is_failure_even_when_upstream_exit_is_success(tmp_path, monkeypatch):
    sf.write(tmp_path / "source.flac", np.zeros(44100), 44100)
    monkeypatch.setattr(rvc_converter, "require_model", lambda *args: {"checkpoint": "personal.pth", "index": "personal.index"})
    monkeypatch.setattr(rvc_converter.subprocess, "run", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="no genero audio"):
        rvc_converter.convert_personal(tmp_path / "source.flac", "user", 0, tmp_path / "voice.flac")
    assert not (tmp_path / "voice.flac").exists()


def test_sample_review_requires_manual_confirmation_for_low_word_match(profile_root):
    from app.main import app
    samples.save_sample("user", {"id": "sample", "status": "review", "alignment": {"coverage": 0.3}})
    with TestClient(app) as client:
        response = client.post("/api/studio/user/samples/sample/review", json={"accept": True})
        assert response.status_code == 409
        response = client.post("/api/studio/user/samples/sample/review", json={"accept": True, "manual": True})
        assert response.status_code == 200
        assert samples.read_sample("user", "sample")["status"] == "accepted"


def test_review_response_and_studio_summary_reflect_saved_decision(profile_root):
    from app.main import app
    item = {"id": "sample", "status": "review", "alignment": {"coverage": 1},
            "prompt_id": "es-01", "language": "es", "mode": "speak",
            "quality": {"active_seconds": 15}, "sha256": "test"}
    samples.save_sample("user", item)
    with TestClient(app) as client:
        for accept, expected, seconds in [(True, "accepted", 15), (True, "accepted", 15),
                                           (False, "excluded", 0)]:
            response = client.post("/api/studio/user/samples/sample/review", json={"accept": accept})
            assert response.status_code == 200
            assert response.json()["status"] == expected
            assert samples.read_sample("user", "sample")["status"] == expected
            summary = client.get("/api/studio/user").json()
            assert summary["samples"][0]["status"] == expected
            assert summary["seconds"]["es"] == seconds


def test_missing_personal_model_blocks_song_conversion(profile_root):
    from app.main import app
    with TestClient(app) as client:
        response = client.post("/api/songs/library/convert", json={"user_id": "user"})
        assert response.status_code == 409
        assert "modelo personal" in response.json()["detail"]


def test_preview_api_passes_pitch_and_rejects_values_outside_an_octave(profile_root, monkeypatch):
    from app.main import app
    from app.api import songs as songs_api
    from app.services import previews
    source = profile_root / "song.wav"
    monkeypatch.setattr(songs_api, "local_songs", lambda: {"song": source})
    calls = []
    def start(user, path, start, language, pitch):
        calls.append((user, path, start, language, pitch))
        return {"status": "trained", "preview": {"pitch_shift": pitch}}
    monkeypatch.setattr(previews, "start_preview", start)
    with TestClient(app) as client:
        body = {"song_id": "song", "language": "en", "start_seconds": 30, "pitch_shift": -12}
        response = client.post("/api/studio/user/preview", json=body)
        assert response.status_code == 200
        assert calls == [("user", source, 30, "en", -12)]
        assert client.post("/api/studio/user/preview", json={**body, "pitch_shift": -13}).status_code == 422
        assert client.post("/api/studio/user/preview", json={**body, "pitch_shift": 13}).status_code == 422


def test_preview_uses_requested_pitch_and_preserves_previous_audio(profile_root, monkeypatch):
    from app.services import previews
    monkeypatch.setattr(previews, "PROFILES", profile_root)
    root = profile_root / "user/models/version/preview"
    root.mkdir(parents=True)
    old = root / "original.wav"
    old.write_bytes(b"previous audition")
    state = {"status": "trained", "approved": False, "version": "version",
             "preview": {"status": "processing", "id": "new", "pitch_shift": -12}}
    monkeypatch.setattr(previews, "decode", lambda *args: np.ones(44100 * 15, dtype=np.float32) * 0.1)
    monkeypatch.setattr(previews, "separate_song", lambda *args: {"vocals": "voice", "instrumental": "music"})
    pitches = []
    def convert(source, user, pitch, destination, approved):
        pitches.append(pitch)
        assert not approved
        return {"pitch_shift": pitch}
    monkeypatch.setattr(previews, "convert_personal", convert)
    monkeypatch.setattr(previews, "mix_final_song", lambda *args: args[-1].write_bytes(b"new audition"))
    previews.run_preview("user", "source", 0, state)
    assert pitches == [-12]
    assert state["preview"]["status"] == "completed"
    assert state["preview"]["output"] == str(root / "new/preview.mp3")
    assert old.read_bytes() == b"previous audition"


def test_cancel_does_not_signal_a_pid_from_persisted_untrusted_state(profile_root, monkeypatch):
    personal_model.save_model("canceltest", {"status": "training", "approved": False, "pid": 12345})
    def unexpected(*args):
        raise AssertionError("Only a tracked subprocess may be signaled")
    monkeypatch.setattr(personal_model.os, "killpg", unexpected)
    assert personal_model.cancel_training("canceltest")["stage"] == "Cancelando entrenamiento"
    personal_model.cancelled.discard("canceltest")
