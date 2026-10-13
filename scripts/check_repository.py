"""Check the Git index for private artifacts, secrets and saved notebook outputs."""
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ("backend/storage/", "backend/models/", "backend/Applio/", "backend/seed-vc/",
           "songs-original/", "songs-voice-replaced/", "exports/", "private/", ".local-repos/", ".codex/", ".agents/", ".cache/", ".tools/")
EXTENSIONS = {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".webm", ".mp4", ".pth", ".pt", ".onnx",
              ".index", ".safetensors", ".bin", ".zip", ".pem", ".key", ".log"}
SECRETS = re.compile(r"(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{50,}|hf_[A-Za-z0-9]{30,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)")


def check():
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    errors = []
    for name in filter(None, names):
        path = Path(name)
        if name.startswith(PRIVATE) or path.suffix.lower() in EXTENSIONS or (path.name.startswith(".env") and path.name != ".env.example") or any(part in {"node_modules", ".next", "__pycache__"} or part.startswith(".venv") for part in path.parts):
            errors.append(f"Private/generated path: {name}")
            continue
        data = subprocess.check_output(["git", "show", f":{name}"], cwd=ROOT)
        if len(data) > 5 * 1024 * 1024:
            errors.append(f"Unexpected large source file: {name}")
            continue
        text = data.decode(errors="replace")
        if SECRETS.search(text):
            errors.append(f"Possible credential: {name}")
        if path.suffix == ".ipynb":
            notebook = json.loads(text)
            if any(cell.get("outputs") or cell.get("execution_count") is not None for cell in notebook["cells"]):
                errors.append(f"Notebook includes saved execution data: {name}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Repository privacy check passed: {sum(bool(name) for name in names)} source files; no audio, models, private paths or notebook outputs.")


if __name__ == "__main__":
    check()
