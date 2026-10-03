# syntax=docker/dockerfile:1

# --- Build the C kernel as a CFFI extension ---
FROM python:3.12-slim AS kernel
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir cffi setuptools
WORKDIR /build
COPY kernel/src/ ./
RUN python cffi_builder.py && cp _emukernel*.so /build/_emukernel.so

# --- Runtime ---
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EMUCRAFT_KERNEL_DIR=/app/kernel/src

WORKDIR /app
COPY web/requirements.txt web/requirements.txt
RUN pip install --no-cache-dir -r web/requirements.txt

COPY gcode/ gcode/
COPY web/ web/
COPY --from=kernel /build/_emukernel.so kernel/src/

RUN useradd --system --no-create-home emucraft
USER emucraft

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1

# Long timeout: simulations of large programs run synchronously in the request
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "330", "--access-logfile", "-", "web.app:app"]
