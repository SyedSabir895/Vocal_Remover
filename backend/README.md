# Vocals Remover API

Self-hosted backend, used for local development or any host that runs Docker. The Hugging Face deployment lives in `../space/` instead.

FastAPI service that splits a song into an instrumental track and a vocals track using [Demucs](https://github.com/adefossez/demucs) (`htdemucs`).

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/upload` | Upload a song (`file` form field). Returns `job_id`. |
| `GET` | `/api/status/{job_id}` | `status` (`queued`, `processing`, `done`, `error`) and `progress` (0-100). |
| `GET` | `/api/download/{job_id}/{stem}` | Download `instrumental` or `vocals` as MP3. |
| `DELETE` | `/api/jobs/{job_id}` | Remove a finished job and its files. |

## Configuration

Set these as environment variables.

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALLOWED_ORIGINS` | `http://localhost:5180,http://127.0.0.1:5180` | Comma-separated frontend URLs allowed to call the API. |
| `MAX_DURATION_SECONDS` | `600` | Longest song accepted. |
| `MAX_ACTIVE_JOBS` | `5` | Queued plus running jobs before new uploads are refused. |

## Run locally

```
python -m venv venv
venv\Scripts\activate
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118 --no-cache-dir
pip install -r requirements.txt --no-cache-dir
uvicorn main:app --port 8000
```

FFmpeg must be installed and on `PATH`.

## Notes

- One song is processed at a time; other uploads wait in a queue.
- Uploaded songs are deleted as soon as processing ends. Results are deleted once the client has fetched them, or after 30 minutes.
- Jobs are kept in memory, so they are lost when the server restarts.
