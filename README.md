# NeMo Switchyard Helm chart

This repository provides a Helm chart to deploy **NVIDIA NeMo Switchyard** and its web configurator on a Kubernetes cluster. The chart bundles the Switchyard proxy, the FastAPI configurator UI, and (optionally) native Prometheus `ServiceMonitor` and Grafana dashboard resources.

## Services

| Service      | Image / build                     | Port | Purpose |
|--------------|-----------------------------------|------|---------|
| switchyard   | built from `v0.2.0` source        | 4000 | LLM proxy + `/metrics` |
| configurator | built from local `configurator/` Dockerfile | 8080 | Web UI to edit `routes.toml` |

*Prometheus and Grafana are assumed to be provided by the cluster (e.g., via `kube-prometheus-stack`). Both integrations are opt-in: setting `serviceMonitor.enabled=true` creates a `ServiceMonitor` so Prometheus scrapes Switchyard, and `grafanaDashboard.enabled=true` ships the dashboard as a ConfigMap with discovery labels for the Grafana sidecar.*

## Building and pushing the images

No prebuilt images are published. `nemo-switchyard` and `nemo-switchyard-configurator` are local build artifacts. On any cluster whose nodes cannot see your local Docker daemon you must build, push, and point the chart at your registry first.

1. **Build** (from the repo root):
   ```bash
   # Switchyard server: multi-stage Rust build of the pinned upstream tag
   docker build -t nemo-switchyard:v0.2.0 switchyard/

   # Configurator: the build context is the repo root (-f), because the
   # image also copies the shared switchyard_config/ package
   docker build -f configurator/Dockerfile -t nemo-switchyard-configurator:v0.2.0 .
   ```
   Build a different upstream release with `--build-arg SWITCHYARD_VERSION=<git-tag>` (see `switchyard/Dockerfile`).

2. **Push** both images to a registry your cluster can reach:
   ```bash
   REGISTRY=ghcr.io/your-org
   docker tag  nemo-switchyard:v0.2.0              $REGISTRY/nemo-switchyard:v0.2.0
   docker tag  nemo-switchyard-configurator:v0.2.0 $REGISTRY/nemo-switchyard-configurator:v0.2.0
   docker push $REGISTRY/nemo-switchyard:v0.2.0
   docker push $REGISTRY/nemo-switchyard-configurator:v0.2.0
   ```

3. **Point the chart at them** via a values file:
   ```yaml
   switchyard:
     image:
       registry: ghcr.io/your-org
       repository: nemo-switchyard
       tag: v0.2.0        # empty = chart appVersion
   configurator:
     image:
       registry: ghcr.io/your-org
       repository: nemo-switchyard-configurator
   imagePullSecrets: []   # e.g. [{ name: regcred }] for private registries
   ```

Clusters that can see your local images (e.g. Rancher Desktop with the dockerd runtime, or after `minikube image load` / `kind load docker-image`) work with the unqualified default image names as-is.

## Quick start (Helm)

1. **Create provider secrets** – each LLM client in your `routes.toml` that uses `api_key_env` needs a Kubernetes `Secret` holding that key. Example for the `openai` client used by `routes.toml.example`:
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

2. **Install the chart** – you can override any defaults via `--set` or a custom values file. All resources deploy into a dedicated `switchyard` namespace which the chart creates for you. The chart ships a minimal placeholder `routes.toml` (one passthrough route to a local endpoint); point it at your real config to get started:
   ```bash
   helm upgrade --install switchyard ./chart \
     -n switchyard --create-namespace \
     --set-file routesToml=switchyard/config/routes.toml.example \
     -f my-values.yaml   # optional custom values
   ```
   You can also edit the configuration later in the configurator UI.
   To deploy into a different (or pre-existing) namespace, set `namespace.name`, or `namespace.name: ""` with `create: false` to use the plain `-n` release namespace.

3. **Access the configurator** – once deployed, the UI is reachable at `http://localhost:<port>` (use `kubectl port-forward` or expose the service as needed):
   ```bash
   kubectl port-forward -n switchyard svc/switchyard-configurator 8080:8080
   ```
   The UI lets you add providers, create routes, and save the configuration. Saving PATCHes the config `ConfigMap` (via the configurator's service account); if the Stakater Reloader is installed, its annotation on the switchyard `Deployment` rolls the pods so new routes are picked up, and every `helm upgrade` rolls them via a config checksum regardless. Provider tokens are supplied as Kubernetes `Secrets` and injected as environment variables and never stored in the `ConfigMap`.

4. **Verify** – check that Switchyard is healthy and serving metrics:
   ```bash
   curl http://localhost:4000/health
   curl http://localhost:4000/v1/models
   ```
   If you enabled the observability values, Prometheus should have a target for `switchyard` and Grafana shows the dashboard.

## Routes

`switchyard/config/routes.toml.example` shows the configuration format: LLM clients, targets, and routes. Six route types are supported:

- **Passthrough** – direct to a single target
- **Random** – weighted A/B split across targets
- **LLM classifier (capability)** – route based on model difficulty
- **LLM classifier (escalation)** – similar to capability but with confirmations
- **Stage router** – selects a tier based on tool/agent signals
- **Advisor gate** – executor runs, advisor reviews, then gates further execution

## Error handling

All API endpoints return JSON `{ "ok": true }` on success. On validation errors the response is `{ "ok": false, "field": "<field>", "error": "<message>" }` with HTTP 400.

## Provider configuration

Providers are defined in the UI (or via the Helm `values.yaml`). Each provider needs:

- `name` – internal identifier used in the routing table
- `envVar` – the environment variable name that will contain the API key (e.g., `MY_PROVIDER_TOKEN`)
- `secret` – the name of the Kubernetes `Secret` that stores the token
- `secretKey` – the key inside the `Secret` (default `token`)

The UI and the chart inject provider tokens as environment variables via `secretKeyRef`; nothing is written to the `ConfigMap` or any file.

## Metrics

Switchyard exposes a set of Prometheus metrics (request/error counters, latency histograms, etc.). When the `ServiceMonitor` is enabled, Prometheus scrapes them automatically, and with `grafanaDashboard.enabled=true` the shipped Grafana dashboard visualises:

- Request rate
- Error rate
- Latency percentiles
- Per-model request and error counts

## Notes

- Switchyard is pre-alpha upstream. Pin/upgrade the version by setting `switchyard.image.tag` in `values.yaml` (and rebuilding the image from the matching `SWITCHYARD_VERSION` build arg).
- The server image builds for `x86-64-v3` (AVX2) on amd64 and `neoverse-n1` on arm64 (rustflags from the upstream `.cargo/config.toml`, inherited via the git clone). Adjust the Dockerfile if you need a different target.
- This deployment is intended for development/testing. For production you should add TLS, stricter RBAC, and external secret management.
