import pytest


@pytest.fixture(autouse=True)
def isolate_persistent_state(tmp_path, monkeypatch):
    # TestClient runs startup recovery; never let tests recover real user jobs.
    from app.services import jobs, personal_model, previews, samples, performances, singing_guides
    from app.api import songs
    for module in (personal_model, previews, samples, singing_guides):
        monkeypatch.setattr(module, "PROFILES", tmp_path / "profiles")
    monkeypatch.setattr(jobs, "JOBS", tmp_path / "jobs")
    monkeypatch.setattr(songs, "JOBS", tmp_path / "jobs")
    monkeypatch.setattr(songs, "STORAGE", tmp_path / "storage")
    monkeypatch.setattr(performances, "STORAGE", tmp_path / "storage")
    monkeypatch.setattr(performances, "OUTPUTS", tmp_path / "outputs")
