FROM python:3.10-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=2 \
    MKL_NUM_THREADS=2
WORKDIR /home/user/app

# CPU-only PyTorch: far smaller than the CUDA build. Swap the index URL for a GPU host.
RUN pip install --no-cache-dir torch==2.7.1 torchaudio==2.7.1 \
    --index-url https://download.pytorch.org/whl/cpu

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the model weights into the image so the first request does not download them.
RUN python -c "from demucs.pretrained import get_model; get_model('htdemucs')"

COPY --chown=user main.py .

EXPOSE 7860
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
