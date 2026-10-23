# --- Build the C kernel (native library; the WebAssembly copy is committed) ---
FROM python:3.12-slim AS kernel
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev make \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY kernel/ kernel/
RUN make -C kernel native

# --- Runtime: pure Python (stdlib server), no compiler needed ---
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EMUCRAFT_KERNEL=/app/lib/libemucraft.so

WORKDIR /app
COPY emucraft/ emucraft/
COPY examples/ examples/
COPY --from=kernel /build/kernel/build/libemucraft.so lib/

# Optional: mount G-code here to list it in the viewer
RUN mkdir /programs && useradd --system --no-create-home emucraft
USER emucraft

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/info')" || exit 1

CMD ["python", "-m", "emucraft", "serve", "--host", "0.0.0.0", "--port", "8000", "--programs", "/programs"]
