import io

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from app.services import samples, singing_guides as guides


def guide_fixture(tmp_path, monkeypatch):
    source = tmp_path / "song.wav"
    wave = np.sin(np.arange(16000 * 24) * (2 * np.pi * 180 / 16000)).astype(np.float32) * .15
    sf.write(source, wave, 16000)
    song = {"song_id": "song1", "name": "song.wav", "source": str(source), "language": "es", "job": {}}
    monkeypatch.setattr(guides, "sources", lambda: [song])
    item = {"id": "song-guide", "song_id": "song1", "name": "song.wav", "language": "es",
            "source_sha256": guides.fingerprint(source), "start_seconds": 0, "duration": 24,
            "text": "Texto aproximado de la guia", "isolated_available": False, "word_count": 8}
    guides.save_state("user", {"status": "ready", "guides": [item], "warnings": [], "progress": 100})
    return item, source, wave


def test_guided_take_keeps_corrected_words_and_provenance_not_song_audio(tmp_path, monkeypatch):
    from app.main import app
    item, source, wave = guide_fixture(tmp_path, monkeypatch)
    submissions = []
    monkeypatch.setattr(samples.validator, "submit", lambda *args: submissions.append(args))
    corrected = "Una nube cruza la ventana abierta"
    take = io.BytesIO()
    sf.write(take, wave, 16000, format="WAV")
    with TestClient(app) as client:
        response = client.post("/api/studio/user/samples", data={
            "prompt_id": item["id"], "guide_id": item["id"], "mode": "sing",
            "pitch_shift": -3, "guide_kind": "mix", "expected_text": corrected,
        }, files={"audio": ("my-take.wav", take.getvalue(), "audio/wav")})
    assert response.status_code == 200
    sample = response.json()
    assert sample["status"] == "queued"
    assert sample["language"] == "es"
    assert sample["guide"]["pitch_shift"] == -3
    assert sample["guide"]["text_source"] == "edited"
    assert sample["expected_text"] == corrected
    assert (samples.directory("user") / sample["filename"]).read_bytes() == take.getvalue()
    assert len(submissions) == 1
    monkeypatch.setattr(samples, "transcribe", lambda *args: {"text": corrected, "chunks": [
        {"text": word, "timestamp": (index, index + .5)} for index, word in enumerate(corrected.split())]})
    samples.validate_sample("user", sample["id"])
    analyzed = samples.read_sample("user", sample["id"])
    assert analyzed["status"] == "review"
    assert analyzed["alignment"]["coverage"] == 1
    assert samples.dataset_status("user")["singing_seconds"] == 0


def test_guide_reference_cannot_be_changed_or_misattributed(tmp_path, monkeypatch):
    from app.main import app
    item, source, _ = guide_fixture(tmp_path, monkeypatch)
    with TestClient(app) as client:
        payload = {"prompt_id": "es-01", "guide_id": item["id"], "mode": "sing"}
        assert client.post("/api/studio/user/samples", data=payload,
                           files={"audio": ("take.wav", b"user audio")}).status_code == 400
        source.write_bytes(b"changed source")
        payload["prompt_id"] = item["id"]
        assert client.post("/api/studio/user/samples", data=payload,
                           files={"audio": ("take.wav", b"user audio")}).status_code == 400
        assert client.get("/api/studio/user/singing-guides/song-guide/audio?pitch=13").status_code == 422
    assert samples.list_samples("user") == []


def test_incomplete_guided_take_is_rejected_before_asr(tmp_path, monkeypatch):
    item, _, wave = guide_fixture(tmp_path, monkeypatch)
    root = samples.directory("user")
    root.mkdir(parents=True)
    sf.write(root / "short.wav", wave[:16000 * 10], 16000)
    samples.save_sample("user", {"id": "short", "filename": "short.wav", "prompt_id": item["id"],
                                "language": "es", "mode": "sing", "status": "queued",
                                "guide": item, "expected_text": item["text"]})
    monkeypatch.setattr(samples, "transcribe", lambda *args: (_ for _ in ()).throw(AssertionError("ASR must not run")))
    samples.validate_sample("user", "short")
    rejected = samples.read_sample("user", "short")
    assert rejected["status"] == "rejected"
    assert any("fragmento completo" in issue for issue in rejected["issues"])


def test_pitch_adjustment_preserves_duration_and_does_not_create_disk_variants(tmp_path, monkeypatch):
    item, source, wave = guide_fixture(tmp_path, monkeypatch)
    root = guides.directory("user")
    sf.write(root / "song-guide-mix.flac", wave[:16000], 16000)
    before = sorted(path.name for path in root.iterdir())
    content = guides.guide_audio("user", item["id"], 3)
    shifted, rate = sf.read(io.BytesIO(content))
    assert len(shifted) == 16000
    assert rate == 16000
    frequency = np.fft.rfftfreq(len(shifted), 1 / rate)[np.argmax(abs(np.fft.rfft(shifted)))]
    assert 211 < frequency < 217
    assert sorted(path.name for path in root.iterdir()) == before
    guides.shifted_audio.cache_clear()


def test_window_selection_avoids_silence_and_overlap():
    wave = np.zeros(16000 * 100, dtype=np.float32)
    wave[16000 * 28:16000 * 96] = .1
    values = np.zeros(5000)
    values[1400:4800] = 180
    windows = guides.candidate_windows({"duration": 100, "wave": wave, "pitch_values": values})
    assert len(windows) >= 2
    assert all(window["start"] >= 16 for window in windows)
    assert all(abs(first["start"] - second["start"]) >= 28
               for index, first in enumerate(windows) for second in windows[index + 1:])


def test_suggested_pitch_never_pushes_user_towards_higher_notes():
    assert guides.recommended_pitch({"median_hz": 100}, {"median_hz": 200}) == 0
    assert guides.recommended_pitch({"median_hz": 400}, {"median_hz": 200}) == -12


def test_catalog_hides_archived_and_wrong_language_songs(tmp_path, monkeypatch):
    item, _, _ = guide_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(guides, "sources", lambda: [])
    assert guides.public_state("user")["guides"] == []
    monkeypatch.setattr(guides, "sources", lambda: [{"song_id": item["song_id"], "name": "song.wav", "language": "en"}])
    assert guides.public_state("user")["guides"] == []


def test_interrupted_preparation_retains_finished_guides(tmp_path, monkeypatch):
    item, _, _ = guide_fixture(tmp_path, monkeypatch)
    state = guides.read_state("user")
    state["status"] = "preparing"
    guides.save_state("user", state)
    guides.recover()
    recovered = guides.read_state("user")
    assert recovered["status"] == "failed"
    assert recovered["guides"] == [item]


def test_automatic_selection_has_one_fragment_per_unique_song(monkeypatch):
    songs = [{"song_id": f"song{index}", "name": f"song{index}.wav", "language": "es",
              "sha256": signature, "pitch_values": np.full(100, 180)}
             for index, signature in enumerate(["same", "same", "different"])]
    monkeypatch.setattr(guides, "personal_range", lambda *args: None)
    monkeypatch.setattr(guides, "song_analysis", lambda song: song)
    monkeypatch.setattr(guides, "candidate_windows", lambda song: [{"start": 20, "duration": 24}, {"start": 60, "duration": 24}])
    monkeypatch.setattr(guides, "extract_guide", lambda user, song, start, duration, personal, manual: {
        "id": f"guide-{song['song_id']}-{start}", "song_id": song["song_id"], "text": "A few different words"})
    state = {"status": "queued", "guides": [], "warnings": []}
    guides.run_preparation("user", songs, state)
    assert state["status"] == "ready"
    assert len(state["selection_ids"]) == 2
    assert len({guide["song_id"] for guide in state["guides"]}) == 2


def test_excluded_song_stays_excluded_across_ids_and_refreshes(tmp_path, monkeypatch):
    item, source, _ = guide_fixture(tmp_path, monkeypatch)
    song = {"song_id": "song1", "name": "song.wav", "language": "es", "source": str(source), "job": {}}
    another = {**song, "song_id": "song2", "name": "other.wav"}
    monkeypatch.setattr(guides, "sources", lambda: [song, another])
    monkeypatch.setattr(samples.validator, "submit", lambda *args: None)
    state = guides.prepare("user", exclude_song_ids=["song1"])
    assert state["guides"] == []
    assert [song["song_id"] for song in state["songs"]] == ["song2"]
    persisted = guides.read_state("user")
    persisted["status"] = "ready"
    guides.save_state("user", persisted)
    monkeypatch.setattr(guides, "sources", lambda: [{**song, "song_id": "reuploaded"}, another])
    state = guides.prepare("user")
    assert [song["song_id"] for song in state["songs"]] == ["song2"]
