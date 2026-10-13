"""CLI shared by local exports and the Colab notebook. Nothing is uploaded."""
import argparse
import json
import time
from pathlib import Path

from app.config import ROOT
from app.services import model_bundles, personal_model


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--kind", choices=("model", "training"), default="model")
    export.add_argument("--output", type=Path, required=True)
    importing = commands.add_parser("import-training")
    importing.add_argument("source", type=Path)
    inference = commands.add_parser("import-model")
    inference.add_argument("source", type=Path)
    train = commands.add_parser("train")
    train.add_argument("--mode", choices=("initial", "fine_tune"), default="fine_tune")
    train.add_argument("--epochs", type=int)
    train.add_argument("--batch-size", type=int, default=4)
    train.add_argument("--require-cuda", action="store_true")
    train.add_argument("--output-dir", type=Path, default=ROOT / "exports")
    for command in (export, importing, inference, train):
        command.add_argument("--user", default="luis_felipe")
    args = parser.parse_args()
    if args.command == "export":
        print(model_bundles.export_bundle(args.user, args.kind, args.output))
    elif args.command == "import-training":
        result = model_bundles.import_training(args.user, args.source)
        print(json.dumps({key: result[key] for key in ("ready", "seconds", "singing_seconds", "missing")}, indent=2))
    elif args.command == "import-model":
        model = model_bundles.import_model(args.user, args.source)
        print(f"Modelo importado {model['version']}; escucha y aprueba una prueba en la web.")
    else:
        if args.require_cuda:
            import torch
            if not torch.cuda.is_available():
                raise ValueError("No hay GPU CUDA. Cambia el runtime; no se entrenara en CPU por accidente.")
            print(f"GPU: {torch.cuda.get_device_name()} | CUDA {torch.version.cuda}", flush=True)
        state = personal_model.start_training(args.user, args.epochs, args.mode, args.batch_size)
        print(f"Training log: {state['log']}", flush=True)
        last = None
        try:
            while True:
                current = personal_model.model_status(args.user)
                progress = (current.get("stage"), current.get("epoch"))
                if progress != last:
                    print(f"{progress[0]} | epoch {progress[1]}/{state['epochs']}", flush=True)
                    last = progress
                if current["status"] != "training":
                    break
                time.sleep(3)
        except KeyboardInterrupt:
            personal_model.cancel_training(args.user)
            raise
        finally:
            personal_model.trainer.shutdown(wait=True)
        if current["status"] != "trained" or current.get("version") != state["version"]:
            raise RuntimeError(current.get("last_training", {}).get("error") or current.get("error") or "Entrenamiento no completado.")
        model_bundles.export_bundle(args.user, "model", args.output_dir / "voice-model.zip")
        model_bundles.export_bundle(args.user, "training", args.output_dir / "voice-training-private.zip")
        print(f"Exportados modelo sin audio y paquete privado para siguientes ajustes en {args.output_dir}")


if __name__ == "__main__":
    main()
