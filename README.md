# NeMo Switchyard Configurator

This repository builds the **web configurator** for NVIDIA NeMo Switchyard: a FastAPI UI that edits the `routes.toml` the Switchyard LLM proxy runs on. It contains the configurator (`configurator/`), the shared config package it uses (`switchyard_config/`), and the vendored upstream Switchyard source (`switchyard/`) used to build the server image.

Deployment on Kubernetes is handled by the Helm chart in [macintoshme/switchyard-helm](https://github.com/macintoshme/switchyard-helm), which installs the Switchyard proxy, this configurator, and optional Prometheus `ServiceMonitor` and Grafana dashboard resources into a dedicated namespace.

## Services

| Service      | Image / build                     | Port | Purpose |
|--------------|-----------------------------------|------|---------|
| switchyard   | `ghcr.io/macintoshme/nemo-switchyard` (fork pipeline) | 4000 | LLM proxy + `/metrics` |
| configurator | built from this repo's `configurator/Dockerfile` | 8080 | Web UI to edit `routes.toml` |

*Prometheus and Grafana are assumed to be provided by the cluster (e.g., via `kube-prometheus-stack`). Both integrations are opt-in chart values: `serviceMonitor.enabled=true` creates a `ServiceMonitor` so Prometheus scrapes Switchyard, and `grafanaDashboard.enabled=true` ships the dashboard as a ConfigMap with discovery labels for the Grafana sidecar.*

## Released artifacts

Every `v*` tag push publishes the configurator image (via GitHub Actions, see `.github/workflows/release.yml`):

| Artifact | Location |
|----------|----------|
| configurator image | `ghcr.io/macintoshme/nemo-switchyard-configurator:<tag>` |
| Helm chart (OCI) | `oci://ghcr.io/macintoshme/charts/switchyard` — published from [macintoshme/switchyard-helm](https://github.com/macintoshme/switchyard-helm) |
| server image | `ghcr.io/macintoshme/nemo-switchyard` — published from [macintoshme/Switchyard](https://github.com/macintoshme/Switchyard) |

Tag the configurator release here **before** tagging the chart repo: the chart's `configurator.image` default follows the chart `appVersion`, and the chart release pipeline refuses a tag whose `values.yaml` points at a configurator image that does not exist yet.

The server image builds from upstream's root Dockerfile: `main` and `sha-<commit>` tags on every push to the fork, plus a `vX.Y.Z` tag per upstream release. Building it yourself from `switchyard/Dockerfile` is only for clusters that cannot reach ghcr.io (see below).

> Note: the published artifacts are publicly pullable. If you fork this repo, packages pushed by your workflows may start **private** — check the visibility in the ghcr.io package settings (or add `imagePullSecrets`) before expecting unauthenticated installs.

## Building and pushing the images (local development)

For local development or clusters that cannot reach ghcr.io, build and push the images yourself. On any cluster whose nodes cannot see your local Docker daemon you must push them to a registry the cluster can reach and point the chart at it.

1. **Build** (from the repo root). Tag the configurator to match the chart's `appVersion` (currently `v0.2.7`); tag the server to match whatever you set as `switchyard.image.tag`. The server binary builds from the pinned upstream tag (`v0.2.0`):
   ```bash
   # Switchyard server: multi-stage Rust build of the pinned upstream tag
   docker build -t nemo-switchyard:v0.2.7 switchyard/

   # Configurator: the build context is the repo root (-f), because the
   # image also copies the shared switchyard_config/ package
   docker build -f configurator/Dockerfile -t nemo-switchyard-configurator:v0.2.7 .
   ```
   Build a different upstream release with `--build-arg SWITCHYARD_VERSION=<git-tag>` (see `switchyard/Dockerfile`).

2. **Push** both images to a registry your cluster can reach:
   ```bash
   REGISTRY=ghcr.io/your-org
   docker tag  nemo-switchyard:v0.2.7              $REGISTRY/nemo-switchyard:v0.2.7
   docker tag  nemo-switchyard-configurator:v0.2.7 $REGISTRY/nemo-switchyard-configurator:v0.2.7
   docker push $REGISTRY/nemo-switchyard:v0.2.7
   docker push $REGISTRY/nemo-switchyard-configurator:v0.2.7
   ```

3. **Point the chart at them** via a values file (see the [switchyard-helm README](https://github.com/macintoshme/switchyard-helm#values) for the full values reference):
   ```yaml
   switchyard:
     image:
       registry: ghcr.io/your-org
       repository: nemo-switchyard
       tag: v0.2.7        # empty = chart appVersion
   configurator:
     image:
       registry: ghcr.io/your-org
       repository: nemo-switchyard-configurator
   imagePullSecrets: []   # e.g. [{ name: regcred }] for private registries
   ```

Clusters that can see your local images (e.g. Rancher Desktop with the dockerd runtime, or after `minikube image load` / `kind load docker-image`) work with locally built images once you clear both `image.registry` values and point the `repository`/`tag` pairs at your local images.

## Quick start (Helm)

1. **Create provider secrets** – each LLM client in your `routes.toml` that uses `api_key_env` needs a Kubernetes `Secret` holding that key. Example for the `openai` client used by `switchyard/config/routes.toml.example`:
   ```bash
   kubectl create secret generic openai-secret \
     --from-literal=token="YOUR_OPENAI_API_KEY"
   ```
   Then reference it in a values file (the `envVar` must match the `api_key_env` in your routes file):
   ```yaml
   providers:
     - envVar: OPENAI_API_KEY
       secret: openai-secret
   ```

2. **Install the chart** – install from the published OCI chart (or clone [switchyard-helm](https://github.com/macintoshme/switchyard-helm) and install from its root). The chart ships a minimal placeholder `routes.toml` (one passthrough route to a local endpoint); point it at your real config to get started:
   ```bash
   helm upgrade --install switchyard \
     oci://ghcr.io/macintoshme/charts/switchyard --version 0.2.7 \
     -n switchyard --create-namespace \
     --set-file routesToml=switchyard/config/routes.toml.example \
     -f my-values.yaml   # optional custom values
   ```
   You can also edit the configuration later in the configurator UI. To deploy into a different (or pre-existing) namespace, set `namespace.name`, or `namespace.name: ""` with `create: false` to use the plain `-n` release namespace.

3. **Access the configurator** – once deployed, the UI is reachable at `http://localhost:<port>` (use `kubectl port-forward` or expose the service as needed):
   ```bash
   kubectl port-forward -n switchyard svc/switchyard-configurator 8080:8080
   ```
   The UI lets you add providers, create routes, and save the configuration. Saving PATCHes the config `ConfigMap` (via the configurator's service account); if the Stakater Reloader is installed, its annotation on the switchyard `Deployment` rolls the pods so new routes are picked up, and every `helm upgrade` rolls them via a config checksum regardless. Provider tokens entered in the UI are persisted into a UI-managed Secret (`<release>-tokens`) that both deployments load via `envFrom`; the `ConfigMap` never contains token values. Disable this with `configurator.tokenSecret.enabled=false` to rely solely on the pre-provisioned `providers:` secretKeyRefs from step 1.

4. **Verify** – check that Switchyard is healthy and serving metrics (port-forward the switchyard service first):
   ```bash
   kubectl port-forward -n switchyard svc/switchyard 4000:4000
   curl http://localhost:4000/health
   curl http://localhost:4000/v1/models
   ```
   If you enabled the observability values, Prometheus should have a target for `switchyard` and Grafana shows the dashboard.

## Routes

`switchyard/config/routes.toml.example` shows the configuration format: LLM clients, targets, and routes. Eight route types are supported:

- **Passthrough** – direct to a single target
- **Random** – weighted A/B split across targets
- **LLM classifier (capability)** – route based on model difficulty
- **LLM classifier (escalation)** – similar to capability but with confirmations
- **LLM classifier (custom)** – custom classifier with arbitrary targets, default target, and a policy selector
- **Stage router** – selects a tier based on tool/agent signals
- **Advisor gate** – executor runs, advisor reviews, then gates further execution
- **Composite** – a classifier determines the tier, then a stage router selects the concrete model

## Error handling

Action endpoints (save, preview, probe, provider/route/model edits) return JSON `{ "ok": true }` on success and `{ "ok": false, "error": "<message>" }` (sometimes with a `"title"` for the dialog heading) on failure — always with HTTP 200. The read-only endpoints (`/api/state`, `/api/status`, `/api/health`, `/api/model-hints`) return their payload directly without an `ok` wrapper.

## Provider configuration

Providers are added in the configurator UI: each needs a `name`, an `endpoint` (OpenAI-compatible `/v1` base URL), and optionally an `api_key_env` (the env var that will hold the token) plus the token itself. Tokens entered in the UI are persisted to the `<release>-tokens` Secret on save (see step 3 above).

Alternatively, pre-provision tokens out of band and wire them via the chart's `providers:` values (the `envVar` must match the `api_key_env` in your routes file):

- `envVar` – the environment variable name that will contain the API key (e.g., `OPENAI_API_KEY`)
- `secret` – the name of the Kubernetes `Secret` that stores the token
- `secretKey` – the key inside the `Secret` (default `token`)

Both mechanisms inject provider tokens as environment variables (`secretKeyRef` or the UI-managed Secret via `envFrom`); nothing is written to the `ConfigMap` or any file.

## Metrics

Switchyard exposes a set of Prometheus metrics (request/error counters, latency histograms, etc.). When the chart's `ServiceMonitor` is enabled, Prometheus scrapes them automatically, and with `grafanaDashboard.enabled=true` the shipped Grafana dashboard visualises:

- Request rate
- Error rate
- Latency percentiles
- Per-model request and error counts

## Notes

- Switchyard is pre-alpha upstream. Pin/upgrade by setting `switchyard.image.tag` to a fork pipeline tag (`sha-<commit>` or an upstream `vX.Y.Z`); the default `main` rolls with every push to the fork.
- The server image builds for `x86-64-v3` (AVX2) on amd64 and `neoverse-n1` on arm64 (rustflags from the upstream `.cargo/config.toml`, inherited via the git clone). Adjust the Dockerfile if you need a different target.
- This deployment is intended for development/testing. For production you should add TLS, stricter RBAC, and external secret management.