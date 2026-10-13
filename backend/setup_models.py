"""Download RVC base weights and reuse the local multilingual Whisper cache."""
import os
import argparse
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
CACHE = BACKEND / "models/huggingface/hub"
os.environ["HF_HOME"] = str(CACHE.parent)
os.environ["HF_HUB_DISABLE_XET"] = "1"

from huggingface_hub import hf_hub_download, snapshot_download


def link_weight(repo, filename, target):
    if target.is_file():
        return
    cached = hf_hub_download(repo, filename, cache_dir=str(CACHE))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    target.symlink_to(os.path.relpath(cached, target.parent))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--training-only", action="store_true")
    parser.add_argument("--skip-pretrained", action="store_true")
    args = parser.parse_args()
    for filename in ("pytorch_model.bin", "config.json"):
        link_weight("IAHispano/Applio", f"Resources/embedders/contentvec/{filename}",
                    BACKEND / "Applio/rvc/models/embedders/contentvec" / filename)
    for filename in (() if args.skip_pretrained else ("f0G40k.pth", "f0D40k.pth")):
        link_weight("IAHispano/Applio", f"Resources/pretrained_v2/{filename}",
                    BACKEND / "Applio/rvc/models/pretraineds/hifi-gan" / filename)
    link_weight("lj1995/VoiceConversionWebUI", "rmvpe.pt",
                BACKEND / "Applio/rvc/models/predictors/rmvpe.pt")
    if not args.training_only:
        print(snapshot_download("openai/whisper-small", cache_dir=str(CACHE),
                            allow_patterns=["config.json", "model.safetensors", "preprocessor_config.json",
                                            "tokenizer*", "vocab.json", "merges.txt", "normalizer.json",
                                            "added_tokens.json", "special_tokens_map.json", "generation_config.json"]), flush=True)
