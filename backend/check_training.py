"""One-epoch runtime check on synthetic audio; never trains the user's profile."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
import uuid

from app.services import personal_model, samples
from app.services.audio import fingerprint

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    args = parser.parse_args()
    source = Path(args.audio)
    root = Path(tempfile.mkdtemp(prefix="rvc-diagnostic-"))
    personal_model.PROFILES = root
    samples.PROFILES = root
    user = "diagnostic"
    directory = samples.directory(user)
    directory.mkdir(parents=True)
    for index in range(6):
        target = directory / f"take{index}{source.suffix}"
        shutil.copy2(source, target)
        samples.save_sample(user, {"id": f"take{index}", "filename": target.name,
                                  "sha256": fingerprint(target), "status": "accepted",
                                  "language": "es" if index < 3 else "en",
                                  "mode": "speak" if index % 2 else "sing"})
    version = "diagnostic-" + uuid.uuid4().hex[:8]
    config = personal_model.APPLIO / "assets/config.json"
    if not config.is_file():
        shutil.copy2(personal_model.APPLIO / "assets/config_template.json", config)
    state = {"version": version, "epochs": 1, "status": "training", "approved": False,
             "dataset_sha256": samples.dataset_fingerprint(user),
             "log": str(root / user / "models" / version / "training.log")}
    personal_model.save_model(user, state)
    personal_model.train_model(user, state, samples.list_samples(user))
    result = personal_model.model_status(user)
    print(json.dumps({"status": result["status"], "error": result.get("error"),
                      "checkpoint": result.get("checkpoint"), "log": result["log"],
                      "diagnostic_root": str(root)}, indent=2))
    raise SystemExit(0 if result["status"] == "trained" else 1)
