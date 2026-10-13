import pytest
from fastapi.testclient import TestClient

from app.services import jobs


@pytest.mark.parametrize("path", ["/api/songs", "/api/songs/"])
def test_upload_large_body_without_redirect_and_list_saved_song(path):
    from app.main import app
    content = b"ID3" + b"audio" * (3 * 1024 * 1024)
    with TestClient(app, follow_redirects=False) as client:
        response = client.post(path, data={"language": "en", "pitch_shift": -12},
                               files={"song": ("my-song.mp3", content, "audio/mpeg")})
        assert response.status_code == 200
        assert "location" not in response.headers
        song = response.json()
        assert song["name"] == "my-song.mp3"
        assert song["status"] == "pending"
        assert song["language"] == "en"
        saved = jobs.read_job(song["song_id"])
        assert saved["pitch_shift"] == -12
        assert client.get(song["original_url"]).content == content
        uploads = client.get("/api/songs/library").json()["uploads"]
        assert [item["song_id"] for item in uploads] == [song["song_id"]]


@pytest.mark.parametrize("filename,content,language,message", [
    ("song.txt", b"audio", "es", "Formato de audio"),
    ("song.mp3", b"", "es", "Cancion vacia"),
    ("song.mp3", b"audio", "fr", "Idioma no valido"),
])
def test_invalid_upload_has_readable_error_without_creating_a_song(filename, content, language, message):
    from app.main import app
    with TestClient(app, follow_redirects=False) as client:
        response = client.post("/api/songs", data={"language": language},
                               files={"song": (filename, content, "audio/mpeg")})
        assert response.status_code == 400
        assert message in response.json()["detail"]
        assert client.get("/api/songs/library").json()["uploads"] == []
