# NeMo Switchyard Helm chart

This repository provides a Helm chart to deploy **NVIDIA NeMo Switchyard** and its web configurator on a Kubernetes cluster. The chart bundles the Switchyard proxy, the FastAPI configurator UI, and (optionally) native Prometheus `ServiceMonitor` and Grafana dashboard resources.

## Services

| Service      | Image / build                     | Port | Purpose |
|--------------|-----------------------------------|------|---------|
| switchyard   | built from `v0.2.0` source        | 4000 | LLM proxy + `/metrics` |
| configurator | built from local `configurator/` Dockerfile | 8080 | Web UI to edit `routes.toml` and `.env` |

*Prometheus and Grafana are assumed to be provided by the cluster (e.g., via `kube‑prometheus‑stack`). The chart creates a `ServiceMonitor` so Prometheus scrapes Switchyard automatically, and a `GrafanaDashboard` CR to load the Switchyard dashboard.*

## Quick start (Helm)

1. **Create provider secrets** – each provider you want to use must have a Kubernetes `Secret` that contains the API key. Example for a provider named `my‑provider`:
   ```bash
   kubectl create secret generic my-provider-secret \
     --from-literal=token="YOUR_API_KEY"
   ```
   In `values.yaml` add the provider entry (the secret name and key are referenced there).

2. **Install the chart** – you can override any defaults via `--set` or a custom values file:
   ```bash
   helm upgrade --install switchyard ./chart \
     -f my-values.yaml   # optional custom values
   ```

3. **Access the configurator** – once deployed, the UI is reachable at `http://localhost:<port>` (use `kubectl port-forward` or expose the service as needed):
   ```bash
   kubectl port-forward svc/$(helm get name switchyard)-configurator 8080:8080
   ```
   The UI lets you add providers, create routes, and save the configuration. Saving updates the `ConfigMap`; the `ServiceMonitor` ensures Prometheus picks up the new metrics, and the `GrafanaDashboard` shows the latest data.

4. **Verify** – check that Switchyard is healthy and serving metrics:
   ```bash
   curl http://localhost:4000/health
   curl http://localhost:4000/v1/models
   ```
   Prometheus should have a target for `switchyard` and Grafana will display the dashboard automatically.

## Routes

`switchyard/config/routes.toml` defines clients, targets, and routes. Six route types are supported:

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

The UI automatically reads the secret and populates `.env` for Switchyard.

## Metrics

Switchyard exposes a set of Prometheus metrics (request/error counters, latency histograms, etc.). When the `ServiceMonitor` is enabled, Prometheus scrapes them automatically and the included Grafana dashboard visualises:

- Request rate
- Error rate
- Latency percentiles
- Per‑model request and error counts

## Notes

- Switchyard is pre‑alpha upstream. Pin/upgrade the version by changing `switchyard.image` in `values.yaml`.
- The chart builds the binary for `x86‑64‑v3` (AVX2) by default; adjust the Dockerfile if you need a different build.
- This deployment is intended for development/testing. For production you should add TLS, stricter RBAC, and external secret management.
