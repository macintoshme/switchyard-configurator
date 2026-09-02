# SPDX-License-Identifier: Apache-2.0
#
# NeMo Switchyard web configurator.
# Serves a browser UI for editing routes.toml / .env and applies changes by
# reusing the switchyard_config package (same logic as configure.py).
#
# Runtime needs (via volume mounts):
#   ./switchyard/config   -> /app/switchyard/config   (routes.toml, writable)
#   ./.env                -> /app/.env                 (writable)
#   ./docker-compose.yml  -> /app/docker-compose.yml   (for `compose restart`)
#   /var/run/docker.sock  -> docker socket (optional, for restart switchyard)

FROM python:3.12-slim

# Docker CLI + compose plugin so the web UI can restart the switchyard
# container through the mounted docker socket. Skipped entirely if the
# socket is not mounted — the restart button just reports "docker unavailable".
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gpg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY configurator/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Core config logic (shared with the web configurator).
COPY switchyard_config /app/switchyard_config
# This web configurator.
COPY configurator /app/configurator

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # A non-root-writable HOME so the docker CLI (used for "restart") works.
    HOME=/tmp
EXPOSE 8080

# The entrypoint drops to the host uid (CONFIGURATOR_UID/GID, default 1000)
# so files written to the bind mounts keep normal ownership on the host, and
# adds the docker socket's group id so the "restart switchyard" button works.
COPY configurator/entrypoint.sh /app/configurator/entrypoint.sh
RUN chmod +x /app/configurator/entrypoint.sh
ENTRYPOINT ["/app/configurator/entrypoint.sh"]
CMD ["uvicorn", "configurator.app:app", "--host", "0.0.0.0", "--port", "8080"]
