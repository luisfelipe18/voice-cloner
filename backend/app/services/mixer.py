from pathlib import Path
import subprocess
from math import gcd

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy.signal import resample_poly


def at_rate(audio, source_rate, target_rate):
    if source_rate == target_rate:
        return audio
    common = gcd(source_rate, target_rate)
    return resample_poly(audio, target_rate // common, source_rate // common, axis=0)


def mix_final_song(converted_vocal_path, original_vocal_path, instrumental_path, output_path):
    vocal, vocal_sr = sf.read(converted_vocal_path, dtype="float32", always_2d=True)
    original, original_sr = sf.read(original_vocal_path, dtype="float32", always_2d=True)
    instrumental, sr = sf.read(instrumental_path, dtype="float32", always_2d=True)
    vocal = at_rate(vocal.mean(axis=1), vocal_sr, sr)
    original = at_rate(original.mean(axis=1), original_sr, sr)
    meter = pyln.Meter(sr)
    target_lufs = meter.integrated_loudness(original)
    vocal_lufs = meter.integrated_loudness(vocal)
    vocal_gain_db = 0.0
    if np.isfinite(target_lufs) and np.isfinite(vocal_lufs):
        vocal_gain_db = float(np.clip(target_lufs - vocal_lufs, -12, 12))
        vocal *= 10 ** (vocal_gain_db / 20)
    length = len(instrumental)
    vocal = np.pad(vocal[:length], (0, max(0, length - len(vocal))))
    mixed = instrumental + vocal[:, None]
    peak = float(np.max(np.abs(mixed)))
    gain = min(1.0, 0.97 / max(peak, 1e-8))
    mixed *= gain
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".mix.wav")
    encoded = destination.with_suffix(".encoding.mp3")
    sf.write(temporary, mixed, sr, subtype="PCM_24")
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(temporary),
                        "-codec:a", "libmp3lame", "-b:a", "320k", str(encoded)], check=True)
        encoded.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
        encoded.unlink(missing_ok=True)
    return {"sample_rate": sr, "duration_seconds": length / sr,
            "channels": mixed.shape[1], "peak": float(np.max(np.abs(mixed))),
            "vocal_gain_db": vocal_gain_db,
            "master_gain_db": float(20 * np.log10(gain))}
