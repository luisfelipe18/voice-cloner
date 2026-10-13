"""Diagnostic only: does not add the input to a personal training dataset."""
import argparse

from app.services.audio import decode
from app.services.prompts import get_prompt
from app.services.samples import compare_words, transcribe

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--prompt", default="es-01")
    args = parser.parse_args()
    prompt = get_prompt(args.prompt)
    result = transcribe(decode(args.audio, 16000), prompt["language"])
    chunks = [{"text": word["text"], "start": word["timestamp"][0],
               "end": word["timestamp"][1]} for word in result["chunks"]]
    comparison = compare_words(prompt["text"], chunks)
    print({"language": prompt["language"], "coverage": comparison["coverage"],
           "timestamped_words": len(chunks), "first_timestamp": chunks[0]["start"],
           "last_timestamp": chunks[-1]["end"]})
