# Agents Overview

This repository provides a lightweight wrapper around the upstream **NVIDIA NeMo Switchyard** project, making it straightforward to deploy and manage Switchyard on a local workstation using Docker.

## Goals
- **Non‑intrusive**: Keep the original Switchyard source untouched so it can be updated (e.g., via a git submodule or direct pull) without merge pain.
- **Docker‑first**: Build Docker images and provide `docker‑compose` configurations for the full stack (Switchyard service, supporting utilities, and optional monitoring tools).
- **Utility agents**: Small helper scripts/agents that:
  - Configure Switchyard runtime parameters.
  - Expose real‑time metrics (CPU/GPU usage, request latency, etc.).
  - Provide convenient commands for common tasks (setup, health‑check, logs).

## Architecture
```
+-------------------+      +-------------------+
|  Upstream Switch- | <-- |  Wrapper repo     |
|  yard source      |      |  (this project)   |
+-------------------+      +-------------------+
          |                         |
          |   Docker build context |
          v                         v
   +----------------+        +-----------------+
   | switchyard-img |        | utility‑agents  |
   +----------------+        +-----------------+
          |
          v
   +-------------------+
   | docker‑compose.yml |
   +-------------------+
```

- **switchyard‑img** – Docker image built from the upstream sources with minimal custom layers (only the utilities are added).
- **utility‑agents** – Small executable scripts (Python, Bash, or Go) that run as side‑car containers or entry‑point helpers. They never modify the core Switchyard code.

## How it works
1. **Fetch upstream** – The repository can pull the latest Switchyard version (e.g., via a git submodule or `git clone`).
2. **Docker build** – A `Dockerfile` copies the upstream code into a base image, installs dependencies, and then adds the wrapper utilities.
3. **Compose** – `docker‑compose.yml` spins up the Switchyard service together with any metric collectors or UI front‑ends.
4. **Agents** – Scripts like `configure.sh`, `metrics_exporter.py`, and `health_check.sh` are mounted or baked into the image. They interact with Switchyard via its REST/GRPC API, leaving the upstream binary untouched.

## Adding new upstream versions
When a new Switchyard release is available:
1. Update the reference (e.g., git submodule commit or repository tag).
2. Run `docker build` again – the Docker layer cache will rebuild only the changed parts.
3. Adjust any wrapper utilities if the upstream API changed; the core Switchyard code remains unchanged.

---

*This file documents the purpose and high‑level design of the agents and Docker workflow for the Switchyard wrapper project.*