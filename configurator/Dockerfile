# SPDX-License-Identifier: Apache-2.0
#
# NeMo Switchyard web configurator.
# Serves a browser UI for editing routes.toml. In Kubernetes it reads and
# persists the configuration by PATCHing the config ConfigMap via the
# in-cluster service account; locally it keeps the file-based behaviour.
#
# Runtime env:
#   CONFIGMAP_NAME          - ConfigMap to read/write (Kubernetes only)
#   CONFIGURATOR_DRAFT_DIR  - writable dir for draft files (emptyDir volume)
#   CONFIGURATOR_PORT       - listen port (default 8080)
#   SWITCHYARD_URL          - switchyard base URL for status checks

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
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
    HOME=/tmp
EXPOSE 8080

CMD ["python", "-m", "configurator.app"]