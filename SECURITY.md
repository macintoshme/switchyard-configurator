# Security Guidelines for Switchyard Configurator

## Overview
This document describes security considerations for running the Switchyard configurator stack (Switchyard server, Prometheus, Grafana, and the FastAPI configurator) in production. It covers Docker socket exposure, secret handling, network exposure, and runtime hardening.

---

## 1. Docker Socket Exposure

### What it is
The configurator container can optionally mount the host Docker socket (`/var/run/docker.sock`). This enables the UI to restart the Switchyard service via `docker compose restart switchyard`.

### Risks
- **Root-level access**: The socket grants the container the same privileges as the Docker daemon on the host, effectively full root access.
- **Container breakout**: A compromised configurator could spawn containers, mount host filesystems, or modify existing containers.

### Mitigations
1. **Avoid mounting the socket in production** unless absolutely required. Manage the Switchyard service with external orchestration (systemd, Kubernetes, CI/CD).
2. **Use the Docker Engine API over TLS**:
   - Run the Docker daemon with `-H tcp://0.0.0.0:2376` and configure TLS certificates.
   - Mount the TLS certificates into the configurator and set `DOCKER_HOST=tcp://host:2376`.
   - Restrict access to the API via firewall rules.
3. **Run as non-root**: The Dockerfile creates a non-root user; ensure the process runs as that user with only needed capabilities.
4. **Restrict socket permissions**: If the socket must be mounted, change its group ownership to a dedicated group and add the configurator user to that group.

---

## 2. Secret Handling

- **Never store secrets in the repository**. Use Docker secrets, Kubernetes secrets, or environment variables injected at runtime.
- **Avoid plain-text `.env` files** in production. If needed for local development, add them to `.gitignore`.
- **Limit environment variable exposure**: Run the configurator with the minimal set of env vars; do not expose secrets to child processes.

---

## 3. Network Exposure

- **Run behind a reverse proxy** (e.g., Nginx, Traefik) that terminates TLS. Do not expose the FastAPI port directly to the internet.
- **Enable HTTPS** and enforce strong cipher suites.
- **Restrict API access**: If the configurator is only for internal use, bind it to `127.0.0.1` or configure firewall rules to limit access.

---

## 4. Runtime Hardening

- **Enable OS-level security**: Use `--security-opt=no-new-privileges` and `--cap-drop=ALL` on every container in the deployment.
- **Set resource limits** (`cpu`, `memory`) for the configurator pod so a single request storm cannot exhaust node resources.
- **Update dependencies on a schedule**: run `pip list --outdated` at least monthly and patch any dependency with a published CVE.
- **Log sanitization**: Ensure logs do not contain secrets; the configurator logs use the `LOG_LEVEL` env var and avoid printing sensitive data.

---

## 5. Monitoring & Auditing

- **Collect metrics** via Switchyard's own `/metrics` endpoint; the chart's `ServiceMonitor` makes Prometheus scrape it when `serviceMonitor.enabled=true`.
- **Audit container logs** for unexpected restarts, failed image pulls, and non-4xx spikes in the request/error counters.
- **Use image scanning** (e.g., Trivy, Clair) in CI to detect known CVEs.

---

## 6. Incident Response

1. **Revoke compromised secrets** immediately and rotate them.
2. **Stop the container** and remove the Docker socket mount if present.
3. **Investigate logs** and run a forensics scan on the host.
4. **Restore from a clean image** built from a trusted base.