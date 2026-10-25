# Moonshot: its web page (default) and, as a separate service, its MCP server. Built from the repository root:
#   docker build -f moonshot/Dockerfile -t moonshot .
#   docker run -p 8000:8000 -v moonshot-web:/data moonshot                       web page and API on port 8000
#   docker run -p 8000:8000 -v moonshot-mcp:/data -e MOONSHOT_TOKEN=... moonshot \
#       python -m moonshot.server --http --host 0.0.0.0 --port 8000             MCP server (/mcp, /files, /health)
# Give each its own /data. Dependencies come from pyproject.toml's moonshot and moonshot-mcp extras.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MOONSHOT_DATA=/data NUMBA_CACHE_DIR=/tmp/numba
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir $(python -c "import tomllib; x = tomllib.load(open('pyproject.toml', 'rb'))['project']['optional-dependencies']; print(*(r for k in ('moonshot', 'moonshot-mcp') for r in x[k] if not r.startswith('emucraft')))")
COPY moonshot/ moonshot/
COPY samples/ samples/
# The page borrows the G-code viewer's 3D viewer, helpers, stylesheet and three.js
COPY emucraft/web/ emucraft/web/

RUN mkdir -p /data && useradd --system --no-create-home moonshot && chown moonshot /data
USER moonshot
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/')" || exit 1
CMD ["python", "-m", "moonshot.web", "--host", "0.0.0.0", "--port", "8000", "--data", "/data"]
