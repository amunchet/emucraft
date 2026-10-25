# Two images from one file:
#   docker build -t emucraft .                         the web app (emucraft serve: G-code viewer + Moonshot page)
#   docker build --target moonshot-mcp -t moonshot-mcp .   Moonshot's MCP server, on its own
# Both run from the source tree in /app; their Python dependencies come from pyproject.toml's extras.

# --- Build the C kernel (native library; the WebAssembly copy is committed) ---
FROM python:3.12-slim AS kernel
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev make \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY kernel/ kernel/
RUN make -C kernel native

# --- Moonshot's MCP server: streamable HTTP on port 8000 (/mcp, /files/..., /health), data in /data ---
#   docker run -p 8000:8000 -v moonshot-data:/data -e MOONSHOT_TOKEN=... -e MOONSHOT_URL=https://host moonshot-mcp
FROM python:3.12-slim AS moonshot-mcp
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MOONSHOT_DATA=/data NUMBA_CACHE_DIR=/tmp/numba
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir $(python -c "import tomllib; x = tomllib.load(open('pyproject.toml', 'rb'))['project']['optional-dependencies']; print(*(r for k in ('moonshot', 'moonshot-mcp') for r in x[k] if not r.startswith('emucraft')))")
COPY moonshot/ moonshot/
COPY samples/ samples/
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "moonshot.server", "--http", "--host", "0.0.0.0", "--port", "8000"]

# --- Web app: pure-Python stdlib server, no compiler needed (last, so it is the default target) ---
FROM python:3.12-slim AS web
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EMUCRAFT_KERNEL=/app/lib/libemucraft.so \
    NUMBA_CACHE_DIR=/tmp/numba

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir $(python -c "import tomllib; x = tomllib.load(open('pyproject.toml', 'rb'))['project']['optional-dependencies']; print(*(r for r in x['moonshot']))")
COPY emucraft/ emucraft/
COPY moonshot/ moonshot/
COPY examples/ examples/
COPY samples/ samples/
COPY --from=kernel /build/kernel/build/libemucraft.so lib/

# /programs: optional G-code to list in the viewer. /data: the Moonshot page's models and runs.
RUN mkdir /programs /data && useradd --system --no-create-home emucraft && chown emucraft /data
USER emucraft
VOLUME /data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/info')" || exit 1

CMD ["python", "-m", "emucraft", "serve", "--host", "0.0.0.0", "--port", "8000", "--programs", "/programs", "--moonshot-data", "/data"]
