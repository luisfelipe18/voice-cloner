"""Checked Applio adapter with bounded FAISS threads on Apple Silicon."""
import argparse
import sys
from pathlib import Path

import torch
import faiss

APPLIO = Path(__file__).resolve().parent / "Applio"
sys.path.insert(0, str(APPLIO))
from rvc.infer.infer import VoiceConverter

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--index", required=True)
    parser.add_argument("--pitch", type=int, default=0)
    args = parser.parse_args()
    torch.set_num_threads(4)
    # FAISS and Torch ship different OpenMP runtimes on macOS. Parallel FAISS
    # search can deadlock at its join barrier; single-thread search avoids it.
    faiss.omp_set_num_threads(1)
    converter = VoiceConverter()
    converter.convert_audio(audio_input_path=args.source, audio_output_path=args.destination,
                            model_path=args.checkpoint, index_path=args.index, pitch=args.pitch,
                            f0_method="rmvpe", index_rate=0.75, protect=0.33,
                            volume_envelope=1.0, export_format="WAV", embedder_model="contentvec")
    if not Path(args.destination).is_file():
        raise RuntimeError("Applio no genero la pista vocal esperada.")
