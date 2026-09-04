# Agents Overview

This repository builds the **FastAPI configurator** for NVIDIA NeMo
Switchyard and the shared Python package it uses. The upstream Switchyard
source is vendored under `switchyard/` and built from a pinned tag
(`v0.2.0`); the configurator under `configurator/` edits `routes.toml`
through the `ConfigMap` installed by the Helm chart.

## Goals

- **Non-intrusive**: keep the upstream Switchyard source untouched so it can
  be re-vendored from a newer tag without merge conflicts.
- **Kubernetes-first**: the Helm chart (now in
  [macintoshme/switchyard-helm](https://github.com/macintoshme/switchyard-helm))
  installs the Switchyard proxy, the configurator UI, an optional Prometheus
  `ServiceMonitor`, and an optional Grafana dashboard ConfigMap into a
  dedicated namespace.
- **Configuration UI**: the configurator (`configurator/app.py`) lets
  operators add providers, build routes, and save the result by PATCHing the
  config `ConfigMap`; provider tokens stay in Kubernetes `Secrets` and are
  injected as environment variables via `secretKeyRef`.

## Layout

- `switchyard/` - vendored upstream source plus the `Dockerfile` that builds
  the server image (multi-stage Rust build; the version is controlled by the
  `SWITCHYARD_VERSION` build arg).
- `configurator/` - FastAPI web UI and its Dockerfile. The build context is
  the repo root because the image also copies the shared `switchyard_config/`
  package.
- `switchyard_config/` - shared Python package for reading, validating, and
  serializing `routes.toml`.
- The Helm chart lives in the separate `switchyard-helm` repo (moved out of
  this repo on 2026-09-04); its release pipeline publishes
  `oci://ghcr.io/macintoshme/charts/switchyard`.

The earlier docker-compose stack and its utility-agents sidecars
(`metrics_exporter.py`, `health_check.sh`) were removed during the Helm
conversion; metrics now come from Switchyard's own `/metrics` endpoint via
the chart's `ServiceMonitor`.

## Adding new upstream versions

1. Update the vendored source under `switchyard/` (or the tag passed as
   `SWITCHYARD_VERSION` in `switchyard/Dockerfile`).
2. Rebuild and push the images (see README, "Building and pushing the
   images").
3. In the `switchyard-helm` repo, update `appVersion` in `Chart.yaml` and
   the default `switchyard.image` tag in `values.yaml`, then bump the chart
   version and tag it (after this repo's configurator image for the same
   tag exists).
