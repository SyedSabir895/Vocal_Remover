import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"

MODEL = "htdemucs"
# Chunk length in seconds. Kept small so a 4GB GPU does not run out of memory.
SEGMENT = "7"
MP3_BITRATE = "320"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_DURATION_SECONDS = int(os.environ.get("MAX_DURATION_SECONDS", 10 * 60))
# Queued + running jobs across all users; further uploads are turned away.
MAX_ACTIVE_JOBS = int(os.environ.get("MAX_ACTIVE_JOBS", 5))
ACTIVE_STATUSES = ("queued", "processing")
# Finished jobs the client never collected are deleted after this long.
JOB_TTL_SECONDS = 30 * 60
ALLOWED_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"}
# Public stem name -> file name Demucs writes.
STEMS = {"instrumental": "no_vocals", "vocals": "vocals"}
ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS", "http://localhost:5180,http://127.0.0.1:5180"
).split(",")

jobs = {}
jobs_lock = threading.Lock()
job_queue = queue.Queue()


def ensure_ffmpeg_on_path():
    """winget installs FFmpeg into a folder that already-open terminals don't see."""
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    links = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links"
    if (links / "ffmpeg.exe").exists():
        os.environ["PATH"] += os.pathsep + str(links)
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        raise RuntimeError("FFmpeg not found. Install it and make sure it is on PATH.")


def update_job(job_id, **fields):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(fields)


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


def describe_failure(log_tail):
    if "out of memory" in log_tail.lower():
        return "The server ran out of memory. Close other apps and try again."
    lines = [line.strip() for line in log_tail.splitlines() if line.strip()]
    return lines[-1][:300] if lines else "Separation failed."


def run_demucs(job_id, source):
    """Run Demucs in its own process so a crash or OOM cannot take the API down."""
    out_dir = OUTPUT_DIR / job_id
    cmd = [
        sys.executable, "-m", "demucs",
        "-n", MODEL,
        "--two-stems=vocals",
        "--segment", SEGMENT,
        "--mp3", "--mp3-bitrate", MP3_BITRATE,
        "-o", str(out_dir),
        "--filename", "{stem}.{ext}",
        str(source),
    ]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)

    log_tail = ""
    while True:
        chunk = proc.stderr.read1(4096)
        if not chunk:
            break
        log_tail = (log_tail + chunk.decode("utf-8", errors="replace"))[-4000:]
        # tqdm redraws its bar with "\r"; the last percentage seen is the current one.
        matches = re.findall(r"(\d{1,3})%\|", log_tail)
        if matches:
            # Hold at 99 until the output files are actually written.
            update_job(job_id, progress=min(int(matches[-1]), 99))
    proc.wait()

    result_dir = out_dir / MODEL
    produced = all((result_dir / f"{name}.mp3").exists() for name in STEMS.values())
    if proc.returncode != 0 or not produced:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise RuntimeError(describe_failure(log_tail))


def worker():
    # A single worker: the GPU can only handle one song at a time.
    while True:
        job_id, source = job_queue.get()
        try:
            with jobs_lock:
                if job_id not in jobs:
                    continue
                jobs[job_id]["status"] = "processing"
            run_demucs(job_id, source)
            update_job(job_id, status="done", progress=100, finished=time.time())
        except Exception as exc:
            update_job(job_id, status="error", error=str(exc), finished=time.time())
        finally:
            source.unlink(missing_ok=True)
            job_queue.task_done()


def remove_job(job_id):
    with jobs_lock:
        jobs.pop(job_id, None)
    shutil.rmtree(OUTPUT_DIR / job_id, ignore_errors=True)


def cleaner():
    while True:
        time.sleep(60)
        cutoff = time.time() - JOB_TTL_SECONDS
        with jobs_lock:
            expired = [
                job_id for job_id, job in jobs.items()
                if job.get("finished") and job["finished"] < cutoff
            ]
        for job_id in expired:
            remove_job(job_id)


@asynccontextmanager
async def lifespan(app):
    ensure_ffmpeg_on_path()
    # Nothing survives a restart: jobs live in memory, so old files are orphans.
    for folder in (UPLOAD_DIR, OUTPUT_DIR):
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=cleaner, daemon=True).start()
    yield


app = FastAPI(title="Vocals Remover", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


def get_job(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found.")
        return dict(job)


def client_ip(request):
    # Behind a reverse proxy the caller's address is in this header.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_capacity(ip):
    with jobs_lock:
        active = [job for job in jobs.values() if job["status"] in ACTIVE_STATUSES]
    if any(job["ip"] == ip for job in active):
        raise HTTPException(429, "You already have a song in progress. Wait for it to finish.")
    if len(active) >= MAX_ACTIVE_JOBS:
        raise HTTPException(503, "Server is busy. Try again in a few minutes.")


@app.get("/")
def health():
    return {"status": "ok", "service": "Vocals Remover API"}


@app.post("/api/upload")
def upload(request: Request, file: UploadFile = File(...)):
    original = Path(file.filename or "song")
    extension = original.suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise HTTPException(400, f"Unsupported file type. Allowed: {allowed}")
    ip = client_ip(request)
    check_capacity(ip)

    job_id = uuid.uuid4().hex
    # Stored under a generated name; the user's file name never touches the disk.
    source = UPLOAD_DIR / f"{job_id}{extension}"
    size = 0
    with source.open("wb") as target:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                target.close()
                source.unlink(missing_ok=True)
                limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
                raise HTTPException(413, f"File too large. Limit is {limit_mb} MB.")
            target.write(chunk)

    duration = probe_duration(source)
    if duration is None:
        source.unlink(missing_ok=True)
        raise HTTPException(400, "Could not read this file as audio.")
    if duration > MAX_DURATION_SECONDS:
        source.unlink(missing_ok=True)
        limit_min = MAX_DURATION_SECONDS // 60
        raise HTTPException(400, f"Song too long. Limit is {limit_min} minutes.")

    display_name = re.sub(r"[^\w\-. ]", "_", original.stem).strip()[:80] or "song"
    with jobs_lock:
        jobs[job_id] = {
            "status": "queued",
            "progress": 0,
            "error": None,
            "name": display_name,
            "ip": ip,
            "finished": None,
        }
    job_queue.put((job_id, source))
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
def status(job_id: str):
    job = get_job(job_id)
    return {"status": job["status"], "progress": job["progress"], "error": job["error"]}


@app.get("/api/download/{job_id}/{stem}")
def download(job_id: str, stem: str):
    job = get_job(job_id)
    if stem not in STEMS:
        raise HTTPException(404, "Unknown stem.")
    if job["status"] != "done":
        raise HTTPException(409, "Job is not finished.")
    path = OUTPUT_DIR / job_id / MODEL / f"{STEMS[stem]}.mp3"
    if not path.exists():
        raise HTTPException(404, "File no longer available.")
    return FileResponse(
        path, media_type="audio/mpeg", filename=f"{job['name']} ({stem}).mp3"
    )


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    job = get_job(job_id)
    if job["status"] in ("queued", "processing"):
        raise HTTPException(409, "Job is still running.")
    remove_job(job_id)
    return {"deleted": True}
