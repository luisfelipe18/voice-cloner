from pathlib import Path

from app.config import MODELS


def separate_song(input_audio_path: str, output_dir: str):
    from audio_separator.separator import Separator

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    separator = Separator(
        output_dir=str(directory), model_file_dir=str(MODELS),
        output_format="FLAC", normalization_threshold=0.95,
        mdx_params={"hop_length": 1024, "segment_size": 256,
                    "overlap": 0.25, "batch_size": 1, "enable_denoise": True},
    )
    separator.load_model("UVR-MDX-NET-Voc_FT.onnx")
    result = {}
    for stem in separator.separate(input_audio_path):
        key = "vocals" if "(vocals)" in stem.lower() else "instrumental"
        result[key] = str(directory / stem)
    if set(result) != {"vocals", "instrumental"}:
        raise RuntimeError("El separador no genero ambas pistas.")
    return result
