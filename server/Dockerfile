# Kokoro TTS server with CUDA, for rootless Docker with the NVIDIA container toolkit.
# Model files are not baked in: mount a directory with kokoro-v1.0.onnx and
# voices-v1.0.bin at /models, and a directory for the socket at /run/kokoro-tts.
FROM python:3.12-slim

# kokoro-onnx depends on the CPU onnxruntime, and both it and onnxruntime-gpu install
# the same "onnxruntime" module: remove the CPU one, then reinstall the GPU one over it.
# The [cuda,cudnn] extras bring the CUDA runtime as pip wheels (no CUDA base image).
RUN pip install --no-cache-dir "kokoro-onnx[gpu]" "onnxruntime-gpu[cuda,cudnn]" \
 && pip uninstall -y onnxruntime \
 && pip install --no-cache-dir --force-reinstall --no-deps \
      "onnxruntime-gpu==$(python -c 'import importlib.metadata as m; print(m.version("onnxruntime-gpu"))')"

COPY server.py /app/server.py

ENV KOKORO_MODEL_DIR=/models \
    KOKORO_SOCKET=/run/kokoro-tts/kokoro-tts.sock \
    ONNX_PROVIDER=CUDAExecutionProvider \
    HOME=/tmp \
    PYTHONUNBUFFERED=1

CMD ["python", "/app/server.py"]
