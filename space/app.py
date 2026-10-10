import json
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import gradio as gr
import spaces
import torch
from demucs.api import Separator
from demucs.audio import save_audio

MODEL = "htdemucs"
# Chunk length in seconds; small enough to also run on a 4GB GPU locally.
SEGMENT = 7
MP3_BITRATE = "320k"
MAX_UPLOAD = "50mb"
MAX_DURATION_SECONDS = 10 * 60
# Result files are removed after this long.
OUTPUT_TTL_SECONDS = 30 * 60
ALLOWED_EXTENSIONS = [".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"]
WORK_DIR = Path(tempfile.gettempdir()) / "vocals_remover"

separator = Separator(model=MODEL, segment=SEGMENT)
# ZeroGPU expects the model to be placed on the GPU at import time, not inside the GPU call.
if torch.cuda.is_available():
    separator.model.to("cuda")


def probe_duration(path):
    """Return the audio duration in seconds, or None if the file is not readable audio."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=codec_name:format=duration",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        info = json.loads(result.stdout or "{}")
        if not info.get("streams"):
            return None
        return float(info["format"]["duration"])
    except (subprocess.SubprocessError, KeyError, ValueError):
        return None


def sweep_old_outputs():
    cutoff = time.time() - OUTPUT_TTL_SECONDS
    for folder in WORK_DIR.iterdir():
        if folder.stat().st_mtime < cutoff:
            shutil.rmtree(folder, ignore_errors=True)


def gpu_seconds(source, out_dir, seconds):
    # GPU time requested from ZeroGPU: model start-up plus a share of the song length.
    return int(15 + seconds / 10)


@spaces.GPU(duration=gpu_seconds)
def run_model(source, out_dir, seconds):
    """Only this part runs on the GPU, so only it counts against the visitor's quota."""
    _, stems = separator.separate_audio_file(Path(source))
    vocals = stems.pop("vocals")
    instrumental = sum(stems.values())
    for name, wav in (("instrumental", instrumental), ("vocals", vocals)):
        save_audio(wav.cpu(), Path(out_dir) / f"{name}.wav", separator.samplerate)


def separate(song, progress=gr.Progress()):
    if not song:
        raise gr.Error("Choose a song first.")
    source = Path(song)
    if source.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise gr.Error(f"Unsupported file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")
    seconds = probe_duration(source)
    if seconds is None:
        raise gr.Error("Could not read this file as audio.")
    if seconds > MAX_DURATION_SECONDS:
        raise gr.Error(f"Song too long. Limit is {MAX_DURATION_SECONDS // 60} minutes.")

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    sweep_old_outputs()
    out_dir = WORK_DIR / uuid.uuid4().hex
    out_dir.mkdir()

    progress(0.1, desc="Removing vocals")
    try:
        run_model(str(source), str(out_dir), seconds)
    except gr.Error:
        # Raised by ZeroGPU itself, e.g. when the visitor's daily quota is used up.
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise gr.Error("Separation failed. Try again in a moment.") from exc

    progress(0.8, desc="Saving files")
    results = []
    for name in ("instrumental", "vocals"):
        wav = out_dir / f"{name}.wav"
        mp3 = out_dir / f"{name}.mp3"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-b:a", MP3_BITRATE, str(mp3)],
            check=True,
        )
        wav.unlink()
        results.append(str(mp3))
    return results


demo = gr.Interface(
    fn=separate,
    inputs=gr.File(label="Song", file_types=ALLOWED_EXTENSIONS, type="filepath"),
    outputs=[
        gr.Audio(label="Instrumental", type="filepath"),
        gr.Audio(label="Vocals", type="filepath"),
    ],
    title="Vocals Remover",
    description="Upload a song, get the instrumental and the vocals as separate tracks. "
    "Up to 50 MB and 10 minutes.",
    api_name="separate",
    flagging_mode="never",
    delete_cache=(600, OUTPUT_TTL_SECONDS),
)

if __name__ == "__main__":
    demo.launch(max_file_size=MAX_UPLOAD)
