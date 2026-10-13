import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app.services import jobs, pipeline
from app.services.mixer import mix_final_song


def test_failed_conversion_never_publishes_original(tmp_path, monkeypatch):
    source = tmp_path / "song.wav"
    source.write_bytes(b"audio")
    monkeypatch.setattr(pipeline, "STORAGE", tmp_path)
    monkeypatch.setattr(pipeline, "OUTPUTS", tmp_path / "outputs")
    monkeypatch.setattr(pipeline, "require_model", lambda *args: {
        "dataset_sha256": "test", "version": "personal-test"})
    monkeypatch.setattr(pipeline, "separate_song", lambda *args: {
        "vocals": str(source), "instrumental": str(source)})
    def fail(*args, **kwargs):
        raise RuntimeError("Conversion unavailable")
    monkeypatch.setattr(pipeline, "convert_voice", fail)
    with pytest.raises(RuntimeError, match="Conversion unavailable"):
        pipeline.process_full_song_pipeline(source, "song", "user")
    assert not list((tmp_path / "outputs").glob("*"))
    assert not list(tmp_path.glob("**/your_vocals.flac"))


def test_mix_resamples_preserves_stereo_and_complete_duration(tmp_path):
    sr, length = 44100, 88200
    instrumental = np.zeros((length, 2), dtype=np.float32)
    instrumental[:, 0] = 0.06 * np.sin(np.arange(length) * 2 * np.pi * 300 / sr)
    instrumental[:, 1] = 0.08 * np.sin(np.arange(length) * 2 * np.pi * 700 / sr)
    original = 0.15 * np.sin(np.arange(length) * 2 * np.pi * 150 / sr)
    vocal_sr = 22050
    vocal = 0.13 * np.sin(np.arange(vocal_sr * 2) * 2 * np.pi * 150 / vocal_sr)
    sf.write(tmp_path / "inst.flac", instrumental, sr)
    sf.write(tmp_path / "orig.flac", original, sr)
    sf.write(tmp_path / "converted.flac", vocal, vocal_sr)
    destination = tmp_path / "mix.mp3"
    metrics = mix_final_song(tmp_path / "converted.flac", tmp_path / "orig.flac",
                             tmp_path / "inst.flac", destination)
    assert destination.is_file()
    assert metrics["duration_seconds"] == 2
    assert metrics["channels"] == 2
    assert metrics["sample_rate"] == sr
    assert metrics["peak"] <= 0.971
    assert not (tmp_path / "mix.mix.wav").exists()


def test_job_failure_is_visible_and_recovery_is_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS", tmp_path)
    jobs.save_job({"song_id": "example", "source": "file.wav", "status": "queued",
                   "user_id": "user", "pitch_shift": 0, "steps": 30,
                   "reference_kind": "talk", "output_name": "output.mp3"})
    def fail(*args, **kwargs):
        raise RuntimeError("Missing model")
    monkeypatch.setattr(jobs, "process_full_song_pipeline", fail)
    jobs.run_job("example")
    assert jobs.read_job("example")["status"] == "failed"
    assert jobs.read_job("example")["error"] == "Missing model"
    jobs.update_job("example", status="processing")
    jobs.recover_interrupted_jobs()
    assert jobs.read_job("example")["status"] == "failed"


def test_api_errors_and_ranges(tmp_path, monkeypatch):
    from app.main import app
    from app.api import songs
    monkeypatch.setattr(jobs, "JOBS", tmp_path)
    output = tmp_path / "output.mp3"
    output.write_bytes(b"0" * 100)
    jobs.save_job({"song_id": "good", "status": "completed", "output": str(output)})
    jobs.save_job({"song_id": "bad", "status": "failed", "error": "Engine error", "progress": 35})
    with TestClient(app) as client:
        response = client.get("/api/songs/bad/status")
        assert response.json()["status"] == "failed"
        assert response.json()["error"] == "Engine error"
        assert client.get("/api/songs/bad/download").status_code == 409
        assert client.get("/api/songs/unknown/status").status_code == 404
        response = client.get("/api/songs/good/download", headers={"Range": "bytes=0-9"})
        assert response.status_code == 206
        assert len(response.content) == 10
        response = client.post("/api/songs/library/convert", json={"user_id": "../escape"})
        assert response.status_code == 400


def test_paths_remain_readable_after_moving_to_docker(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "OUTPUTS", tmp_path)
    assert config.localize_path("/old/checkout/songs-voice-replaced/song.mp3") == tmp_path / "song.mp3"
