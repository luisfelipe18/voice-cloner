import io
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app.services import performances as service
from app.services.audio import decode, fingerprint


def wav(seconds=3, stereo=False, frequency=170):
    time = np.arange(int(44100 * seconds)) / 44100
    audio = .15 * np.sin(2 * np.pi * frequency * time)
    if stereo:
        audio = np.column_stack((audio, .1 * np.sin(2 * np.pi * 650 * time)))
    stream = io.BytesIO()
    sf.write(stream, audio, 44100, format="WAV", subtype="PCM_16")
    return stream.getvalue()


class Immediate:
    def submit(self, callback, *args):
        callback(*args)


@pytest.fixture
def studio(monkeypatch):
    from app.main import app
    monkeypatch.setattr(service, "validator", Immediate())
    monkeypatch.setattr(service, "renderer", Immediate())
    monkeypatch.setattr(service, "transcribe", lambda audio, language: {
        "text": "hola nuevo", "chunks": [
            {"text": "hola", "timestamp": (0.2, 0.5)},
            {"text": "nuevo", "timestamp": (1.5, 2.0)},
        ]})
    with TestClient(app) as client:
        song = client.post("/api/songs", data={"language": "es"},
                           files={"song": ("diagnostic.wav", wav(5), "audio/wav")}).json()
        prefix = f"/api/performances/{song['song_id']}"
        assert client.put(f"{prefix}/lyrics", json={"lyrics": "hola mundo nuevo", "language": "es"}).status_code == 200
        yield client, prefix, song["song_id"]


def upload(client, prefix, mode="sing", **fields):
    response = client.post(f"{prefix}/takes", data={"mode": mode, **fields},
                           files={"audio": ("take.wav", wav(), "audio/wav")})
    assert response.status_code == 200, response.text
    return client.get(prefix).json()


@pytest.fixture
def personal_voice(monkeypatch):
    model = {"version": "personal-test", "checkpoint_sha256": "checkpoint-test",
             "dataset_sha256": "dataset-test"}
    calls = []
    monkeypatch.setattr(service, "require_model", lambda user_id: model)

    def convert(source, user_id, pitch_shift, destination, expected_model, progress):
        calls.append({"source": source, "destination": destination, "model": expected_model,
                      "user_id": user_id, "pitch_shift": pitch_shift})
        progress(40, "Convirtiendo con tu modelo RVC personal")
        samples = len(decode(source))
        sf.write(destination, .1 * np.sin(np.arange(samples) * 2 * np.pi * 820 / 44100), 44100)
        return {"personal_model": True, "model_version": model["version"],
                "checkpoint_sha256": model["checkpoint_sha256"], "engine": "test-rvc"}

    monkeypatch.setattr(service, "convert_personal", convert)
    return calls


def ready_mix(client, prefix):
    item = upload(client, prefix)
    identity = item["selected"]["sing"]
    assert client.patch(f"{prefix}/takes/{identity}/words",
                        json={"word_index": 1, "confirmed": True}).status_code == 200
    assert client.post(f"{prefix}/backing/upload", files={
        "audio": ("backing.wav", wav(5, stereo=True), "audio/wav")}).status_code == 200
    return identity


def test_independent_read_and_sing_recognition_with_word_positions(studio):
    client, prefix, _ = studio
    first = upload(client, prefix, "read")
    second = upload(client, prefix, "sing")
    assert first["selected"]["read"] == second["selected"]["read"]
    assert second["selected"]["read"] != second["selected"]["sing"]
    for take in second["takes"]:
        assert [word["text"] for word in take["words"]] == ["hola", "mundo", "nuevo"]
        assert [word["status"] for word in take["words"]] == ["matched", "missing", "matched"]
        assert take["words"][1]["estimated"] is True
        assert take["words"][1]["start"] == .5
        assert take["words"][1]["end"] == 1.5


def test_individual_repeat_preserves_original_and_only_resolves_one_word(studio, monkeypatch):
    client, prefix, song_id = studio
    item = upload(client, prefix, "read")
    original = service.take_from(service.read(song_id), item["selected"]["read"])
    path = service.directory(song_id) / original["filename"]
    checksum = fingerprint(path)
    monkeypatch.setattr(service, "transcribe", lambda audio, language: {
        "text": "mundo", "chunks": [{"text": "mundo", "timestamp": (.2, .8)}]})
    item = upload(client, prefix, "read", parent_id=original["id"], word_index=1)
    correction = item["takes"][-1]
    assert [word["text"] for word in correction["words"]] == ["mundo"]
    assert fingerprint(path) == checksum
    response = client.patch(f"{prefix}/takes/{correction['id']}/correction", json={"approved": True})
    assert response.status_code == 200
    words = response.json()["takes"][0]["words"]
    assert [word["status"] for word in words] == ["matched", "corrected", "matched"]
    assert client.get(correction["audio_url"]).status_code == 200


def test_manual_confirmation_for_false_asr_error_and_persistent_history(studio):
    client, prefix, _ = studio
    item = upload(client, prefix)
    take = item["takes"][0]
    assert client.patch(f"{prefix}/takes/{take['id']}/words", json={"word_index": 1, "confirmed": True}).status_code == 200
    assert client.get(prefix).json()["takes"][0]["words"][1]["status"] == "confirmed"
    changed = client.put(f"{prefix}/lyrics", json={"lyrics": "otra letra propia", "language": "en"}).json()
    assert changed["takes"][0]["stale"]
    assert len(changed["takes"]) == 1
    blocked = client.post(f"{prefix}/takes", data={"mode": "sing", "parent_id": take["id"],
                          "word_index": 1, "target_start": .5, "target_end": 1.5},
                          files={"audio": ("word.wav", wav(1), "audio/wav")})
    assert blocked.status_code == 409


def test_singing_repeat_requires_valid_interval_and_approval(studio, monkeypatch):
    client, prefix, _ = studio
    item = upload(client, prefix)
    take = item["takes"][0]
    invalid = client.post(f"{prefix}/takes", data={"mode": "sing", "parent_id": take["id"],
                          "word_index": 1, "target_start": 2, "target_end": 1},
                          files={"audio": ("word.wav", wav(1), "audio/wav")})
    assert invalid.status_code == 400
    monkeypatch.setattr(service, "transcribe", lambda audio, language: {"text": "algo", "chunks": []})
    item = upload(client, prefix, parent_id=take["id"], word_index=1, target_start=.5, target_end=1.5)
    correction = item["takes"][-1]
    assert client.patch(f"{prefix}/takes/{correction['id']}/correction", json={"approved": True}).status_code == 409
    # The 3-second repetition would require excessive stretching into a 1-second slot.
    assert client.patch(f"{prefix}/takes/{correction['id']}/correction", json={"approved": True, "manual": True}).status_code == 409


def test_real_mix_uses_personal_model_after_patches_and_preserves_stereo(studio, monkeypatch, personal_voice):
    client, prefix, song_id = studio
    item = upload(client, prefix)
    take = item["takes"][0]
    original_path = service.directory(song_id) / service.read(song_id)["takes"][0]["filename"]
    original_hash = fingerprint(original_path)
    monkeypatch.setattr(service, "transcribe", lambda audio, language: {
        "text": "mundo", "chunks": [{"text": "mundo", "timestamp": (.1, .9)}]})
    response = client.post(f"{prefix}/takes", data={"mode": "sing", "parent_id": take["id"],
                           "word_index": 1, "target_start": .5, "target_end": 1.5},
                           files={"audio": ("word.wav", wav(1, frequency=360), "audio/wav")})
    assert response.status_code == 200
    correction = client.get(prefix).json()["takes"][-1]
    assert client.patch(f"{prefix}/takes/{correction['id']}/correction", json={"approved": True}).status_code == 200
    response = client.post(f"{prefix}/backing/upload", files={"audio": ("backing.wav", wav(5, stereo=True), "audio/wav")})
    assert response.status_code == 200
    backing, rate = sf.read(service.read(song_id)["backing"]["path"])
    assert rate == 44100 and backing.shape[1] == 2
    assert not np.allclose(backing[:, 0], backing[:, 1])
    commands = []
    run = service.subprocess.run

    def record_run(command, **kwargs):
        commands.append(command)
        return run(command, **kwargs)

    monkeypatch.setattr(service.subprocess, "run", record_run)
    assert client.post(f"{prefix}/mix", json={"take_id": take["id"], "offset_ms": -100, "cleanup": True}).status_code == 200
    export = client.get(prefix).json()["exports"][-1]
    assert export["status"] == "completed", export
    content = client.get(export["url"])
    assert content.status_code == 200 and len(content.content) > 10000
    path = Path(service.read(song_id)["exports"][-1]["path"])
    assert abs(len(decode(path)) / 44100 - 5) < .1
    patched = decode(service.directory(song_id) / "exports" / export["id"] / "patched.wav")
    original_audio = decode(original_path)
    assert np.allclose(patched[:20000], original_audio[:20000], atol=1e-5)
    assert not np.allclose(patched[25000:60000], original_audio[25000:60000])
    assert fingerprint(original_path) == original_hash
    assert path.parent.name == "interpretations"
    assert len(personal_voice) == 1
    call = personal_voice[0]
    assert call["source"].name == "clean.wav"
    assert call["user_id"] == "luis_felipe" and call["pitch_shift"] == 0
    assert call["model"]["version"] == "personal-test"
    mixer_command = next(command for command in commands if "-filter_complex" in command)
    assert mixer_command[mixer_command.index("-i") + 1] == str(call["destination"])
    assert export["conversion"]["personal_model"] is True
    assert export["progress"] == 100
    assert client.get(export["vocal_url"]).status_code == 200
    assert "vocal_path" not in export and "path" not in export
    proof = json.loads(path.with_suffix(".json").read_text())
    assert proof["engine"] == "personal-rvc-performance"
    assert proof["conversion"]["checkpoint_sha256"] == "checkpoint-test"
    assert proof["converted_vocals_sha256"] == fingerprint(call["destination"])
    assert proof["conversion_source_sha256"] != proof["converted_vocals_sha256"]
    assert proof["corrections"] == [correction["id"]]


def test_mix_requires_approved_personal_model_before_queueing(studio):
    client, prefix, _ = studio
    identity = ready_mix(client, prefix)
    response = client.post(f"{prefix}/mix", json={"take_id": identity})
    assert response.status_code == 409
    assert not client.get(prefix).json()["exports"]


def test_failed_personal_conversion_never_mixes_or_publishes_raw_take(studio, personal_voice, monkeypatch):
    client, prefix, song_id = studio
    identity = ready_mix(client, prefix)

    def fail(*args, **kwargs):
        raise RuntimeError("RVC unavailable")

    monkeypatch.setattr(service, "convert_personal", fail)
    assert client.post(f"{prefix}/mix", json={"take_id": identity}).status_code == 200
    export = client.get(prefix).json()["exports"][-1]
    assert export["status"] == "failed" and "RVC unavailable" in export["error"]
    assert "url" not in export and "vocal_url" not in export
    assert client.get(f"{prefix}/exports/{export['id']}/audio").status_code == 404
    assert client.get(f"{prefix}/exports/{export['id']}/vocals").status_code == 404
    assert not list(service.OUTPUTS.glob("**/*.mp3"))
    assert len(service.read(song_id)["takes"]) == 1


def test_converter_rejects_a_model_changed_since_queueing(monkeypatch, tmp_path):
    from app.services import rvc_converter
    monkeypatch.setattr(rvc_converter, "require_model", lambda *args: {
        "version": "changed", "checkpoint_sha256": "different", "dataset_sha256": "different"})
    with pytest.raises(ValueError, match="modelo personal cambio"):
        rvc_converter.convert_personal(tmp_path / "input.wav", "user", 0, tmp_path / "output.flac",
                                       expected_model={"version": "requested", "checkpoint_sha256": "original",
                                                       "dataset_sha256": "original"})
    assert not list(tmp_path.glob("*.flac"))


def test_mix_rejects_pending_words_and_reading_take(studio):
    client, prefix, _ = studio
    item = upload(client, prefix)
    assert client.post(f"{prefix}/mix", json={"take_id": item["selected"]["sing"]}).status_code == 409
    item = upload(client, prefix, "read")
    assert client.post(f"{prefix}/mix", json={"take_id": item["selected"]["read"]}).status_code == 400


def test_recovery_marks_interrupted_new_work_without_losing_takes(studio):
    _, _, song_id = studio
    item = service.read(song_id)
    item["takes"] = [{"id": "diagnostic", "status": "analyzing"}]
    item["backing"] = {"status": "processing"}
    item["exports"] = [{"id": "diagnostic", "status": "processing"}]
    service.save(item)
    service.recover()
    item = service.read(song_id)
    assert item["takes"][0]["status"] == "failed"
    assert item["backing"]["status"] == "failed"
    assert item["exports"][0]["status"] == "failed"


def test_word_timing_without_asr_timestamps_is_explicitly_uncertain():
    words = service.word_alignment("hola mundo", [{"text": "hola", "timestamp": (None, None)}], 4)
    assert words[0]["status"] == "uncertain"
    assert words[0]["estimated"]
    assert words[0]["start"] == 0 and words[0]["end"] == 2


def test_english_typographic_contractions_are_single_words():
    words = service.word_alignment("I\u2019m here, don\u2019t go", [
        {"text": "I'm", "timestamp": (0, .2)},
        {"text": "here", "timestamp": (.2, .5)},
        {"text": "don't", "timestamp": (.5, .8)},
        {"text": "go", "timestamp": (.8, 1)},
    ], 1)
    assert len(words) == 4
    assert all(word["status"] == "matched" for word in words)
