"""Install pinned Applio sources and the project's training changes, idempotently."""
import shutil
import subprocess
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
REVISION = "324f4d89c3e0e8dc0e555a3d53784e3282c16e09"


def install(destination=None):
    destination = Path(destination or BACKEND / "Applio")
    if not destination.exists():
        subprocess.run(["git", "clone", "https://github.com/IAHispano/Applio.git", str(destination)], check=True)
        subprocess.run(["git", "-C", str(destination), "checkout", REVISION], check=True)
    revision = subprocess.check_output(["git", "-C", str(destination), "rev-parse", "HEAD"], text=True).strip()
    if revision != REVISION:
        raise RuntimeError("Applio has a different revision; preserve it and install in a clean checkout.")
    patch = BACKEND / "vendor/applio-training.patch"
    command = ["git", "-C", str(destination), "apply"]
    if subprocess.run(command + ["--reverse", "--check", str(patch)], capture_output=True).returncode:
        subprocess.run(command + ["--check", str(patch)], check=True)
        subprocess.run(command + [str(patch)], check=True)
    shutil.copy2(BACKEND / "vendor/finetuning.py", destination / "rvc/train/finetuning.py")
    config = destination / "assets/config.json"
    if not config.exists():
        shutil.copy2(destination / "assets/config_template.json", config)
    print(f"Pinned Applio + personal fine-tuning ready: {destination}")


if __name__ == "__main__":
    install()
