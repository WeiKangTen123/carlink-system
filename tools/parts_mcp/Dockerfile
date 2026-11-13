# The parts-catalogue worker: the "parts-worker" service in
# infra/docker-compose.prod.yml (build context: this folder, tools/parts_mcp).
#
# It claims catalogue look-up jobs from the api over the compose network
# (http://api:8000) and reads the Microfiches catalogues (BMW, MINI, Honda):
# the only container that contacts a parts site. Its page cache and catalogue
# store live in /data, a named volume, so a car looked up once costs nothing
# the next time, across rebuilds.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PARTS_MCP_DATA_DIR=/data

WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY requirements-worker.txt /tmp/requirements-worker.txt
RUN pip install --no-cache-dir -r /tmp/requirements-worker.txt \
    && rm /tmp/requirements-worker.txt

# A user of its own. /data is created here, owned by it, so the named volume
# mounted there starts out writable (Docker copies the owner into a new volume).
RUN groupadd --system --gid 10001 parts \
    && useradd --system --uid 10001 --gid parts --home-dir /data --no-create-home \
       --shell /usr/sbin/nologin parts \
    && mkdir -p /data \
    && chown parts:parts /data

# The package itself (tests, data and PC files stay out: see .dockerignore).
# Owned by root, read-only to the worker.
COPY . /app/parts_mcp

USER parts

# --stay-up: without a token (look-up not set up yet) it says so once and
# idles, instead of exiting and being restarted, and logging, every minute.
CMD ["python", "-m", "parts_mcp.worker", "--stay-up"]
