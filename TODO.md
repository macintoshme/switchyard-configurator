# TODO

_Last updated: 2026-09-03_

## Maintenance protocol — read before working on anything in this repo

This file is the **single source of truth** for outstanding work. Anyone
(human or AI agent) working through these items must keep the file current as
they go — a stale TODO is worse than none.

1. **Start here.** Before beginning any task, read this file top to bottom and
   pick the topmost unchecked item of the highest-priority section (sections
   and items are ordered by priority).
2. **Mark progress in real time.** When you start an item, append
   `— IN PROGRESS (YYYY-MM-DD)` to it. When it is finished, change `[ ]` to
   `[x]` and append `— done (YYYY-MM-DD)`.
3. **Verify before checking off.** Every item has a *Verify* step. An item
   only counts as done when that verification passes. If you cannot verify
   (e.g. no cluster access), say so next to the checkbox instead of checking
   it off silently.
4. **Record discoveries immediately.** If work on an item reveals a new bug,
   a wrong assumption, or a hidden dependency, add or edit items in this file
   right away — do not keep findings in chat, memory, or side notes.
5. **Blocked items** get a `BLOCKED: <reason>` annotation; skip them and
   continue with the next item.
6. **Never delete items.** Obsolete items get struck through (`~~text~~`)
   with a one-line reason; completed items stay checked for history.
7. **Reordering is allowed but explicit.** If you move an item between
   sections, leave a short note saying why.
8. **Update the `Last updated` date** at the top on every edit.
9. **Keep the item format** when adding new items: checkbox + bold title,
   context with `file:line` references, a *Fix* description, and a *Verify*
   command.

---

## 1. Chart fixes (from critical review, 2026-09-02)

Current state (2026-09-02): **P0, P1, P2, and P3 complete** — the chart
renders, `helm lint` is fully clean (no INFO/ERROR), all resources pass
`kubectl apply --dry-run=server` (in a `restricted` PodSecurity namespace),
the configurator is Kubernetes-native, the chart is renamed `switchyard`
with standard labels/NOTES/schema/tests, and everything is verified live on
the Rancher Desktop cluster. Section 1 chart work is done except the GitHub
Actions pipelines item. Re-verify the baseline after every change:
`helm lint chart && helm template chart`.

### P0 — make the chart installable

- [x] **Add the missing `serviceMonitor` values** — done (2026-09-02)
  `chart/templates/prometheus-servicemonitor.yaml:1,8,17` reads
  `.Values.serviceMonitor.enabled` / `.additionalLabels` / `.interval`, but
  `chart/values.yaml` has no `serviceMonitor` key — every
  install/template/lint run fails with `nil pointer evaluating interface
  {}.enabled`.
  *Fix:* add to `values.yaml`:
  ```yaml
  serviceMonitor:
    enabled: false   # opt-in; requires a Prometheus Operator install
    interval: 30s
    additionalLabels: {}
  ```
  *Verify:* `helm lint chart && helm template chart` succeed with defaults.

- [x] **Replace provider-token plumbing with `secretKeyRef` env vars** — done (2026-09-02)
  One change removes three defects:
  - `chart/templates/configmap.yaml:12-18`: broken template syntax
    (`(lookup ...).data (default ...)` — renders as "data is not a method but
    has arguments" whenever a provider has a `secret`); it copies provider
    tokens **in plaintext out of Secrets into a ConfigMap**; and `lookup`
    breaks first-install, `--dry-run`, `helm template`, and GitOps renderers.
  - `chart/templates/switchyard/deployment.yaml:27-29`: `envFrom` on the
    config ConfigMap creates env vars literally named `routes.toml` / `.env`
    (whole file contents as values). The real provider variables
    (e.g. `MY_PROVIDER_TOKEN`) are never injected, so switchyard cannot
    authenticate to any provider.
  *Fix:* drop the `.env` key from the ConfigMap, drop `envFrom`, and render
  one `env:` entry per `providers[]` item using `valueFrom.secretKeyRef`
  (name = `.secret`, key = `.secretKey` default `token`). Keep `providers`
  in values as envVar-to-secret metadata only.
  *Verify:* `helm template chart --set 'providers[0].envVar=MY_TOKEN,providers[0].secret=my-secret'`
  renders a `secretKeyRef` env entry and no `.env` ConfigMap key; no
  `lookup` remains in the templates.

### P1 — configurator: decide, then fix

- [x] **DECISION: make the configurator Kubernetes-native or remove it** — done (2026-09-02): Option A chosen and implemented.
  The app was docker-compose-era: it wrote local files
  (`configurator/state.py`) and "restarted switchyard" by shelling out to
  `docker compose restart`. Implemented as Option A (K8s-native rewrite):
  - `switchyard_config/kube.py` (new): in-cluster adapter — reads/patches
    the config ConfigMap via the pod's service account; `kubernetes`
    imports are lazy so local dev/tests run without the package.
  - `configurator/state.py`: `_load_initial()` reads `routes.toml` +
    `provider_meta.json` from the ConfigMap when in cluster (local file
    mode unchanged); `save()` PATCHes the ConfigMap
    (`_save_to_configmap`) — no more `.env` writes, tokens come from
    Secrets via `secretKeyRef`; snapshot/preview report ConfigMap refs.
  - `switchyard_config/endpoints.py`: `is_switchyard_running()` probes
    `SWITCHYARD_URL/health` (chart sets it to the switchyard Service);
    `restart_switchyard()` no longer shells out to docker — returns an
    informational message (reloader handles rollout).
  - `switchyard_config/constants.py`: drafts redirect to
    `CONFIGURATOR_DRAFT_DIR` (emptyDir `/data` in the chart);
    `SWITCHYARD_URL` env; dead `COMPOSE_FILE` removed.
  - `switchyard_config/routes.py`: `parse_routes_text()` extracted (text
    twin of `load_existing_routes`) with round-trip tests
    (`tests/test_core_features.py`).
  - `configurator/Dockerfile`: dropped docker-ce-cli and
    `entrypoint.sh` (deleted); CMD is `python -m configurator.app` (honors
    `CONFIGURATOR_PORT`); `kubernetes` added to requirements.
  *Verify:* pytest 19 passed; `helm template` renders SA/Role/RoleBinding/
  Service/Deployment; `kubectl apply --dry-run=server` accepted all
  resources; UI save path exercised via import smoke test.

- [x] **Fix fatal `emptyDir` mount over `/app`** — done (2026-09-02): the
  deployment was rewritten; the only volume is an emptyDir at `/data`
  (draft dir), nothing mounts over `/app`.
  *Verify:* rendered manifest mounts no volume at `/app` — confirmed via
  `helm template`.

- [x] **Create or remove the referenced ServiceAccount** — done (2026-09-02):
  `chart/templates/configurator/serviceaccount.yaml` +
  `rbac.yaml` added. Role grants get/patch/update on the config ConfigMap
  only (`resourceNames`-scoped; list/watch omitted because `resourceNames`
  cannot restrict them).
  *Verify:* `helm template chart | kubectl apply --dry-run=server -f -`
  accepted SA + Role + RoleBinding + Deployment.

### P1 — security hardening

- [x] **Add pod security and disable unneeded API access** — done (2026-09-02)
  Both deployments now set `runAsNonRoot: true`,
  `seccompProfile: RuntimeDefault`,
  `containers.securityContext: {allowPrivilegeEscalation: false,
  capabilities.drop: [ALL]}`; the configurator also pins
  `runAsUser/runAsGroup` from its values; switchyard sets
  `automountServiceAccountToken: false`. `readOnlyRootFilesystem` was NOT
  forced — unverified whether the binaries tolerate it (not required for
  `restricted` PSA; revisit if wanted).
  *Verify:* the cluster's `default` namespace enforces
  `pod-security.kubernetes.io/enforce=restricted`; `kubectl apply
  --dry-run=server` accepted both Deployments with zero warnings.

### P2 — functional correctness

- [x] **Make port values real or remove them** — done (2026-09-02)
  Both images now honor their port env at runtime:
  - switchyard: `switchyard/Dockerfile` CMD is
    `sh -c 'exec switchyard-server ... --port ${SWITCHYARD_PORT:-4000}'`
    (`exec` keeps the server as PID 1); the deployment passes
    `SWITCHYARD_PORT` from `.Values.switchyard.port` unconditionally.
  - configurator: CMD is `python -m configurator.app`, which honors
    `CONFIGURATOR_PORT` (deployment already passes it).
  Probes, Services, `SWITCHYARD_URL`, and the ServiceMonitor all render from
  the same values, so an override now reaches every reference.
  *Verify:* rebuilt `nemo-switchyard:v0.2.0` locally and confirmed via
  `docker run` that the server listens on a non-default `SWITCHYARD_PORT`
  (4711 → `/health` 200) and the default 4000; `helm lint` clean;
  `helm template --set switchyard.port=4711` wires env/probes/Service/
  ServiceMonitor consistently; `kubectl apply --dry-run=server` accepted.

- [x] **Image hygiene** — done (2026-09-02)
  `chart/values.yaml` now splits both images into `registry`/`repository`/
  `tag`; the switchyard tag defaults to `{{ .Chart.AppVersion }}` (`v0.2.0`)
  when empty; the configurator tag is pinned (`v0.2.0` — it versions with
  this repo, not upstream). New `switchyard.image` / `configurator.image`
  helpers in `_helpers.tpl` assemble the reference (registry optional);
  top-level `imagePullSecrets: []` is rendered into both deployments.
  Also rebuilt the local images so they match the sources: note the
  configurator build context is the **repo root**
  (`docker build -f configurator/Dockerfile .` — it COPYs
  `switchyard_config/` too, so `docker build configurator/` fails).
  *Verify:* `helm template chart --set switchyard.image.registry=...` renders
  a fully-qualified image plus pull secrets on both deployments; defaults
  render `nemo-switchyard:v0.2.0` / `nemo-switchyard-configurator:v0.2.0`;
  `helm lint` clean; valid YAML; `kubectl apply --dry-run=server` accepted.
  Fresh configurator image contains `kube.py`, imports, and honors
  `CONFIGURATOR_PORT` (9090 → `/api/health` 200 in docker run).

- [x] **Document building and pushing the images** — done (2026-09-02)
  README gained a "Building and pushing the images" section (build, tag,
  push, values overrides, local-cluster shortcut). Discovery while
  live-testing the quick start on the Rancher Desktop cluster: the default
  `routesToml` was empty, and switchyard-server **refuses to boot on an
  empty config** (`missing field schema_version` → `missing field targets`
  → `at least one algorithm route is required`), so a default install
  crash-looped. Fixed by shipping a minimal valid default (one keyless
  passthrough route; server boots without the endpoint being reachable)
  and documenting `--set-file routesToml=...` for real configs.
  *Verify:* live `helm upgrade --install switchyard ./chart -n default` on
  the rancher-desktop cluster (dockerd runtime sees the local images):
  both pods Running/Ready, port-forward → `/health` 200, `/v1/models`
  lists `default-model`, configurator `/api/health` 200. Note: the release
  is still installed there for hands-on use.

- [x] **Dedicated namespace by default** — done (2026-09-02, user-requested)
  New `namespace: {create: true, name: switchyard}` values + a
  `switchyard.namespace` helper: every resource (incl. RBAC subjects, test
  hooks, ServiceMonitor, dashboard) renders into that namespace, and the
  chart creates the Namespace itself (labeled; removed on uninstall).
  `namespace.name: ""` falls back to the plain `-n` release namespace.
  The configurator needed no changes — it discovers its namespace from the
  pod's service-account file (`switchyard_config/kube.py:42`).
  *Verify:* lint clean; default render puts all 10 resources + the Namespace
  in `switchyard`, `--set namespace.name=` puts them in the release ns;
  live: reinstalled via `helm install switchyard ./chart -n switchyard
  --create-namespace`, pods Running 0 restarts, `/health` 200,
  `/v1/models` serves `default-model`, both `helm test` suites Succeeded,
  NOTES show the resource namespace.

- [x] **Provider modal: key visibility toggle + hide the Env var field** — done (2026-09-02, user-requested)
  The show/hide API key button now swaps its label (`show` ↔ `hide`,
  aria-label kept in sync) instead of only toggling the input type. The
  Env var input is gone from the modal: for providers provisioned via
  the UI the env var is auto-derived from the local name when a key is
  typed (`my-provider` → `MY_PROVIDER_API_KEY`; an env var already set
  on an existing provider — e.g. from an older config — is preserved so
  edits never silently rename it, and it is kept when the key field is
  cleared since the runtime key comes from a Kubernetes Secret). A hint
  under the API key field names the env var that routes.toml will read
  and explains that typed keys are only used for probing.
  *Verify:* jsdom provider-modal test (empty key → empty env var, key →
  derived name, custom env var preserved with/without key, hint text,
  toggle label swap) — 7/7 pass; api/UI suites still pass; served by the
  live pod after rollout.

- [x] **Busy indicator on async action buttons** — done (2026-09-02, user-requested)
  New `busyButton(btn, label, fn)` helper in `configurator/static/app.js`:
  while an async action runs, the button is disabled, shows an inline CSS
  spinner (`.spinner` + `@keyframes spin` in `style.css`, with a
  `prefers-reduced-motion` fallback) and a busy label; the original label
  is restored afterwards. The disabled guard also makes double-clicks a
  no-op while a request is in flight (previously two rapid clicks fired
  two probes). Applied to: Test & Fetch Models, Refresh all (modal +
  providers pane), Save Provider, Save Route, Save Configuration, Restart
  switchyard, and Preview files (the last three previously had manual
  disable/label swapping, now unified). The probe status line also got
  `aria-live="polite"` so completion is announced to screen readers.
  *Verify:* extended `/tmp/opencode/click_test.js` with a held-promise
  probe: in flight the button is disabled with spinner + "Testing..." and
  the status line reads "Probing..."; a second click during flight fires
  no extra request; after completion the button is restored — 24/24
  pass; all other frontend suites still pass.

- [x] **Client-side request timeouts (zombie port-forward tunnel)** — done (2026-09-02, user report)
  User clicked "Test & Fetch Models" and the spinner ran for minutes.
  Pod logs showed zero probe requests (uvicorn only logs completed
  requests) and no stuck outbound connections — the request never
  reached the server: the browser's port-forward tunnel had silently
  died (pod replacement mid-session), and `fetch` on such a connection
  never settles, so even the error-handling in `api()` never fired.
  *Fix:* `api()` now arms an `AbortController` per request — default
  60s, probe 30s (server-side candidate scan worst case is ~15-20s),
  initial `/api/state` load 15s — and reports
  "request timed out after Ns - is the port-forward tunnel to the
  cluster still alive?" instead of hanging. The timer covers headers
  and body (cleared only after `resp.json()`).
  *Verify:* `api_test.js` gained a zombie-tunnel simulation (fetch that
  only rejects on abort): times out in ~150ms with the clear error;
  every recorded fetch carries an `AbortSignal`; `click_test.js`
  asserts the probe request is signal-wired. 48 checks across all four
  frontend suites pass.

- [x] **Hide the dead "Restart switchyard" button on Kubernetes** — done (2026-09-02, user request)
  `POST /api/restart` is a stub on Kubernetes (`switchyard_config/endpoints.py:148`):
  it returns "restarts are handled automatically" because saving updates
  the ConfigMap and Stakater Reloader rolls the Deployment. A button
  that only pops that message is misleading — it looks like an action
  but never does anything.
  *Fix:* the chart now sets `DEPLOYMENT_PLATFORM=kubernetes` on the
  configurator container (`chart/templates/configurator/deployment.yaml`),
  `status()` surfaces it as `platform` (`configurator/state.py:979`),
  and the review pane swaps the button for a hint line ("Restarts are
  automatic: ... Without Reloader: kubectl rollout restart
  deployment/<switchyard>") when `platform === "kubernetes"`. Local /
  compose deployments keep the button. `renderStatus()` now stores
  `S.status` and is awaited by `load()` so the platform is known before
  the first tab render. Chart bumped 0.2.0 -> 0.2.1 (version +
  appVersion + configurator image tag).
  *Verify:* new pytest `test_status_reports_deployment_platform`
  (env-var round trip, 20 pass); `ui_smoke.js` gained three checks —
  button present without platform, hidden with `platform:
  "kubernetes"`, hint text rendered. 50 checks across all four
  frontend suites pass. Live: env var visible in pod, `/api/status`
  returns `platform: "kubernetes"`.

- [x] **Helm 4 upgrade conflicts with configurator-written ConfigMap** — done (2026-09-02, discovered while shipping 0.2.1)
  First `helm upgrade` after a user save failed: "conflict with
  \"OpenAPI-Generator\" using v1: .data.routes.toml". Helm 4 upgrades via
  server-side apply and the chart still claimed `routes.toml` from
  install, while the configurator's save (Kubernetes python client — its
  User-Agent becomes the field manager "OpenAPI-Generator") had taken
  over the field. `--take-ownership` would have reset the user's saved
  config, so it was not an option.
  *Fix:* `chart/templates/configmap.yaml` renders `routes.toml` only
  while the ConfigMap doesn't exist (`lookup` guard) — after install the
  configurator owns the content and Helm stops asserting it. Offline
  `helm template` still renders the default (lookup is nil offline), so
  CI renders are unaffected.
  *Verify:* `helm lint` clean; `helm upgrade --dry-run=server` no longer
  reports conflicts; live upgrade to 0.2.1 rolled both deployments and
  the saved vLLM config survived (`llm_clients.vLLM` still in the
  ConfigMap; `/api/state` shows `local` + `vLLM` with no manual restore).

- [x] **Code-maintained seed ConfigMap ("maintain config in code")** — done (2026-09-02, user request)
  User proposal: a second ConfigMap maintained in code; at boot the
  configurator copies it into the runtime ConfigMap when that one is
  missing, ignores it when it has not changed since the runtime CM was
  last written, and clobbers the runtime CM when it has. Refined to
  compare by content hash instead of timestamps (a no-op
  `helm upgrade` re-stamps the seed CM via server-side apply and would
  otherwise clobber UI-saved config although nothing changed in code).
  *Fix:* new Helm-owned `switchyard-config-seed` ConfigMap
  (`chart/templates/configurator/seedconfigmap.yaml`) rendered from
  `values.routesToml` on every upgrade — the configurator never writes
  it, so it can never conflict. `kube.py` gained a pure decision table
  `plan_seed_sync()` plus `sync_seed()` (called from
  `state.py::_load_initial` before loading): the runtime CM carries the
  sha256 of the seed content it last synced from in the
  `switchyard.nvidia.com/seed-checksum` annotation —
  missing runtime CM → recreate from seed (new `create` RBAC verb;
  resourceNames cannot scope create, so it is a separate rule);
  no `routes.toml` yet → fill from seed; hash matches → no-op (UI edits
  win); hash differs → seed changed in code → clobber (and drop stale
  `provider_meta.json`); annotation absent (CM predates the feature) →
  adopt-stamp without clobbering so existing installs keep their saved
  config. The configurator Deployment's old `checksum/config` annotation
  (dead since the lookup guard) was replaced by
  `checksum/seed-config: sha256(values.routesToml)`, so a seed change
  rolls the configurator and the sync applies automatically.
  Chart bumped to 0.2.2.
  *Verify:* 7 new unit tests for the decision table
  (`tests/test_seed_sync.py`, 27 pass total); `helm lint`/`template`
  clean (seed CM rendered, RBAC + env wired). Live round-trip on the
  release: (1) upgrade with default seed → "adopted without
  overwriting", vLLM preserved, annotation stamped; (2) seed changed
  via `--set-file` → configurator rolled, runtime CM clobbered to the
  new content, annotation updated; (3) runtime CM deleted → recreated
  from seed on configurator restart (RBAC create works, no load
  errors); (4) same-values upgrade → no rollout, manual restart →
  zero sync actions, vLLM intact. Final state: the user's saved config
  is the maintained seed (`--set-file`), restored end-to-end by the
  feature itself.

- [x] **Models tab: per-model capabilities & `extra_body` + hints definitions** — done (2026-09-03, user request)
  The user wanted per-model settings (capabilities like vision, free
  request params) in a dedicated "Models" tab rather than inline in the
  provider panels. Upstream v0.2.0 merges a target's `extra_body`
  into every outgoing request body, so that is the persistence layer.
  *Fix:* `ConfigState.model_extras` (model id → dict) round-trips
  through `[targets.*].extra_body` in `routes.toml`
  (`switchyard_config/routes.py`); `ConfigManager.set_model_extras()`
  validates (bare-key names, scalar/nested JSON values, size caps) and
  `snapshot().models` carries per-model rows (self provider skipped).
  UI: new Models tab with chips for current settings, per-model
  Settings modal with tri-state capability selects (upstream's 11-flag
  vocabulary), free-form key/value rows (auto/string/number/boolean/
  json), and one-click suggestions from a baked-in hints definitions
  file (`configurator/model_hints.toml`, `[[hint]]` entries: regex
  `pattern`, `description`, `suggest` map; first match wins). Chart:
  optional `modelHints` values override (ConfigMap over
  `MODEL_HINTS_FILE`, `checksum/model-hints` rollout annotation).
  Hint matching happens in the backend (`_models_summary` attaches the
  matched hint per row) because the patterns are Python-flavored
  regexes (`(?i)` inline flags) that JS RegExp rejects — found via the
  jsdom suite.
  *Verify:* pytest `tests/test_model_extras.py` (12 tests: TOML
  round-trip incl. the hand-edited compose `extra_body` style,
  set/clear + validation, self-provider skip, hint attach, hints
  loader edge cases), 41 pass total; `models_test.js` jsdom suite (19
  checks: table render, chips, modal, tri-state, save POST payload,
  apply-suggestions merge); all five frontend suites pass. Live on
  0.2.3: `/api/model-hints` serves the baked definitions; the
  Qwen3.8-27B-FP8 row matches its hint; POST `/api/models/extras` →
  preview emits `[targets.target_1.extra_body]` → save PATCHes the CM
  → configurator restart re-parses extras with native types; vision
  passthrough through the service still answers "Red" with the
  extras present.

- [x] **Models tab: clearer suggestion-match feedback (green tints + restore)** — done (2026-09-03, user request)
  The user wanted the modal to make the hint-match state obvious: tint
  the "all suggested values are set" box green when the fingerprint's
  defaults matched, tint every capability row that is not "unset"
  green, make the box react when selections stop matching the
  defaults, and add a restore button inside it.
  *Fix:* matching is now value-aware — a suggested key set to a
  different value counts as "differing", not "set" (previously only
  key presence was checked, so a blocked suggestion still showed
  "all set"). `suggestionState()` (app.js) computes
  unset/differing/matches per model; the hint box gets the `matched`
  class (green border + tint, `style.css`) with "All suggested values
  are set.", red chips like "images: block (suggested allow)" for
  diverged values, "Apply suggestions" (fills unset only) and
  "Restore to suggestions" (overwrites every suggested key) in both
  states; capability rows with an explicit value get the `set` green
  tint; the table's Suggested column shows "N value(s) differ" for
  value mismatches and "all set" only on exact match.
  *Verify:* `models_test.js` extended to 30 checks (matched tint,
  set-row tints, divergence flips the box + reports the differing
  value, restore returns values and re-tints, table differs/all-set
  states); all five frontend suites and 41 pytest pass. Live on 0.2.4:
  new `app.js` served, saved Qwen3.8 extras intact through the
  upgrade, no SSA conflicts.

- [x] **Helm 4 upgrade conflicts with configurator-written ConfigMap, round 2: labels** — done (2026-09-03, discovered while shipping 0.2.3)
  First 0.2.3 upgrade failed with SSA conflicts on
  `.metadata.labels.helm.sh/chart` and `.metadata.labels.
  app.kubernetes.io/version` against field manager "OpenAPI-Generator"
  — the seed-sync **create** path (`kube.sync_seed()`) had copied the
  seed ConfigMap's full label set when it recreated the runtime CM
  during 0.2.2 verification, so the configurator's API client became
  the owner of the two version-coupled labels. `--take-ownership`
  does not address SSA conflicts in Helm 4 (it is about legacy
  annotations); the correct one-time remediation is
  `--force-conflicts`.
  *Fix:* `kube._stable_labels()` filters `helm.sh/chart` and
  `app.kubernetes.io/version` out of the labels the create path
  copies — stable discovery labels (name, instance, component,
  managed-by) are kept, version-coupled ones stay with Helm. Patch
  and sync paths never touch labels.
  *Verify:* new pytest `test_recreate_labels_drop_version_coupled_ones`
  (41 pass); live: `--force-conflicts` upgrade to 0.2.3 took the
  labels back (data untouched — the lookup-guarded manifest asserts
  no `data`), and two subsequent no-flag upgrades to the same version
  completed without conflicts.

- [ ] **GitHub Actions pipelines for images and chart artifacts** — IN PROGRESS (2026-09-02)
  The repo will be hosted on GitHub, so CI must produce the release
  artifacts end to end: build and push both images (switchyard multi-stage
  Rust build, configurator) and package/publish the Helm chart (OCI
  artifact to ghcr.io or a gh-pages chart index), versioned from git tags.
  Extends backlog 8️⃣ (lint/test jobs); tag/version sync with
  `Chart.appVersion` relates to backlog 7️⃣'s replacement item.
  *Fix:* workflows under `.github/workflows/`
  (`docker/build-push-action`, `helm package` + push or chart-releaser).
  *Verify:* a tag push builds and publishes both images plus the chart,
  and the README install steps work against the published artifacts.

### P3 — docs and chart hygiene

- [x] **Correct README claims** — done (2026-09-02, P3 pass)
  - `GrafanaDashboard` CR promise at `README.md:12` replaced with the actual
    opt-in mechanism (see dashboard item below).
  - Services table: dropped the stale `.env` mention (configurator only edits
    `routes.toml`); `routes.toml` reference now points at the existing
    `routes.toml.example`.
  - Quick start step 1 is now copy-paste coherent with step 2: concrete
    `openai-secret` + `providers: [{envVar: OPENAI_API_KEY, ...}]` matching
    `api_key_env` in `routes.toml.example`.
  - "Grafana will display the dashboard automatically" and the Metrics
    section now conditional on the opt-in values.
  - Reloader sentence corrected: Stakater Reloader rolls pods only if
    installed; every `helm upgrade` rolls via checksum regardless.
  - Verified the AVX2 note against upstream `.cargo/config.toml`
    (`target-cpu=x86-64-v3` amd64, `neoverse-n1` arm64 — inherited via the
    git clone in the Dockerfile) and documented both targets.
  *Verify:* quick-start commands checked live on the RD cluster (install,
  port-forward, /health, /v1/models); `--set-file` path dry-run-checked.

- [x] **Ship or delete the Grafana dashboard** — done (2026-09-02)
  Shipped, opt-in: `chart/templates/grafana-dashboard.yaml` renders
  `chart/files/grafana/dashboards/switchyard.json` as a ConfigMap
  (`<fullname>-dashboard`) via `.Files.Glob(...).AsConfig`, labeled
  `grafana_dashboard: "1"` for the Grafana sidecar (kube-prometheus-stack
  default) plus `grafanaDashboard.additionalLabels`; gated behind
  `grafanaDashboard.enabled` (default false, next to `serviceMonitor`).
  *Verify:* `helm template --set grafanaDashboard.enabled=true` emits the
  ConfigMap with the JSON payload and discovery labels.

- [x] **Chart conventions** — done (2026-09-02)
  - Chart renamed `switchyard-helm` → `switchyard` (`chart/Chart.yaml`);
    version bumped 0.1.0 → 0.2.0 (breaking: resource names/labels changed);
    added `kubeVersion: ">=1.25.0-0"` and an `icon` (upstream
    `assets/logo.png`).
  - Helpers rewritten helm-create-style: `switchyard.name`,
    `switchyard.fullname` (contains-collapse + name/fullnameOverride), and a
    parameterized `switchyard.labels`/`switchyard.selectorLabels` taking
    `(dict "root" $ "component" ...)` — standard label set
    (`helm.sh/chart`, `app.kubernetes.io/{name,instance,version,component,
    managed-by}`), `configurator.labels` removed; Services/selectors use
    `selectorLabels` (component-scoped: server/configurator).
  - Checksum annotation `checksum/config` on BOTH Deployments' pod templates:
    a ConfigMap-only change now rolls pods with no Reloader. **Verified
    live**: changed model id in `routesToml` → `helm upgrade` → new pod
    rolled out and `/v1/models` served `default-model-2` (reverts the
    earlier crash-loop evidence above).
  - `NOTES.txt` (port-forward + `helm test` hints, opt-in sections) —
    **discovery: helm v4 requires `templates/NOTES.txt`**, the v3 chart-root
    location is silently ignored (v4 `action.go` only matches
    `<chart>/templates/NOTES.txt`; confirmed via `helm create` layout).
  - `values.schema.json` (types, enums for serviceType/pullPolicy, provider
    required keys) — verified helm rejects bad values (e.g. `serviceType:
    BadValue`).
  - Chart tests: `templates/tests/test-connection.yaml` — hook ConfigMap +
    curl pod (curlimages/curl:8.11.1) checking `/health` and `/api/health`
    via the Services, hardened (runAsNonRoot + explicit `runAsUser: 100`
    because the image `USER` is non-numeric `curl_user`, readOnlyRootFS, no
    SA token). **Both suites pass live in ~4s.** ⚠️ Known upstream issue:
    helm v4.2.1 `helm test` hangs forever AFTER the suites complete (stuck
    in "waiting for resources to be deleted" during hook cleanup; the hook
    resources ARE deleted; helm's own 5m timeout never fires). Not
    chart-fixable — run `helm test` under an external `timeout` until helm
    fixes the delete-wait.
  - Resource guidance comments added to both deployments' `resources: {}` in
    values.yaml (probes already existed). PDB/HPA deferred.
  *Verify:* `helm lint` fully clean (icon clears the last INFO — no
  INFO/ERROR at all); `helm template` valid YAML (10 docs default);
  live uninstall+reinstall on RD cluster (selector immutability forced a
  clean reinstall for the local release), pods Running 0 restarts,
  `helm test` suites Succeeded, NOTES render via `helm get notes`.

---

## 2. Pre-existing backlog — configurator codebase

(Carried over from the earlier planning doc; **audited against the code on
2026-09-02** — completed/obsolete items marked, one bug discovered. Chart
work in section 1 still takes priority because the chart is currently
uninstallable.)

Note on the removed script: the original `configure.py` TUI no longer exists
(`switchyard_config/app.py` is a stub: "TUI app removed – functionality now
provided only via the web configurator"). No items below target the removed
script itself — they concern the surviving `switchyard_config/` package and
the web `configurator/`, which share that logic.

The items are ordered by impact. After each step the repository should be **committed and tested** before moving to the next.

### ‼️ DISCOVERED (2026-09-02, user report): pasted diff fragment broke the whole web UI — done (2026-09-02)
`configurator/static/app.js:635-646` contained a raw diff fragment (12
lines prefixed with `+`, plus a commented-out "original line retained for
reference") inside `setSelectVal`, left by a "cline checkpoint" commit
before the initial helm-chart commit. The resulting `SyntaxError:
Unexpected token 'const'` killed the entire script: blank Overview pane
and dead tab buttons (all pane content is JS-rendered; the web UI had
never actually worked in a browser — prior verification was API-level
only).
*Fix:* rewrote `setSelectVal` cleanly (the intended DOM-built `<select>`
with an `aria-label` from the preceding `<label>`, replacing the
innerHTML string); verified no other diff-marker lines exist in
`static/`.
*Verify:* `node --check configurator/static/app.js` passes; headless
jsdom smoke test against the live `/api/state` payload renders the
Overview and switches/renders all three tabs (Providers, Routes, Review
& Save); rebuilt configurator image rolled out and the pod-served
`app.js` passes `node --check`.

### ‼️ DISCOVERED (2026-09-02, user report): frontend `api()` helper broke edits, refresh, and error surfacing — done (2026-09-02)
Three defects in `configurator/static/app.js`'s `api()` fetch helper:
(1) any call with a body was force-set to `method: "POST"`, clobbering the
explicit `PUT` on provider/route edit saves → `405 Method Not Allowed`
(user-visible: "can't edit a provider"; confirmed in pod access logs);
(2) both "Refresh Models" call sites sent `GET` to the POST-only
`/api/providers/refresh` → 405; (3) `fetch` had no error handling — a
network-level failure (e.g. port-forward tunnel dying when the
configurator pod is replaced mid-session) rejects the promise silently
and the UI hangs forever on "Probing endpoint..." (user-visible on the
provider probe; the server never logs the request because it never
completes).
*Fix:* respect `opts.method` when a body is present; `POST`-ify the two
refresh call sites; wrap `fetch` in try/catch returning
`{ok:false, error:"Network error: ..."}`.
*Verify:* jsdom regression test of `api()` (PUT+body, DELETE,
body→POST, no-opts→GET, explicit POST, JSON body, fetch rejection →
ok:false) — 7/7 pass; full UI smoke still 8/8; live: PUT edit and
refresh now return 200 (previously 405), probe returns in ~20 ms.
User later independently reported the symptom this caused ("had to
click the test button and the save button twice") — first click failed
silently (dead tunnel) or with a 405 toast (method clobbering).
Re-verified after the report with a dedicated single-click jsdom test
(`/tmp/opencode/click_test.js`): one click on Test → exactly one probe
request + checklist rendered; one click on Save → exactly one POST
(add) / PUT (edit), modal closes; no listener stacking across modal
re-opens — 9/9 pass alongside the other suites (31 checks total).

### ‼️ DISCOVERED (2026-09-02, user report): provider Edit button dead for every provider except the first — done (2026-09-02)
`configurator/static/app.js`'s per-row Edit handler passed
`stateProviderIndex(i)` — a *state index number* — where
`openProviderModal(provider, index)` expects a provider *object*.
`[...provider.available_models]` then throws
`TypeError: provider.available_models is not iterable` for any state
index ≥ 1 (truthy number → property access on a number), so the modal
never opens: "click edit on my vllm provider, nothing happens". The
first provider (state index 0) "worked" only because `0` is falsy —
the spread was skipped and the modal opened with an **empty model
checklist** (present since the initial commit). Two related latent
defects fixed in the same handlers: the modal populated fields and
saved via `PROV.editingIdx` using the *display* row index against
`S.state.providers`, and Delete called `/api/providers/{displayIdx}`
— both target the wrong provider whenever the self provider
(`is_self`, `switchyard_config/state.py:152`) appears in state.
*Fix:* map once per click (`const si = stateProviderIndex(i)`, bail if
`si < 0`) and pass `(S.state.providers[si], si)` to the modal and `si`
to the DELETE URL.
*Verify:* extended `/tmp/opencode/click_test.js` — vLLM (row 1) edit
opens the modal with name/endpoint/model checklist; local (row 0) edit
now shows its model (was `local/0` boxes, now `local/1`); self-provider
variant: display row 1 edits/deletes state index 2. Confirmed the test
catches the original bug by reverting the fix (5 FAILs incl. the exact
TypeError). Fixed code: 18/18; all other frontend suites still pass.

### ‼️ DISCOVERED (2026-09-02): duplicate `ROUTE_REGISTRY` shadows all handlers
`switchyard_config/route_registry.py:531-541` registers all 8 handlers, but a
stale second definition at `:546-549` (only `PassthroughHandler`) overrides
it. `generate_toml` looks routes up via `ROUTE_REGISTRY.get(route.type)`
(`switchyard_config/routes.py:596-600`), so every non-passthrough route type
misses the registry and falls back to the legacy path. Leftover from the
partial 1️⃣ migration; item 9️⃣'s registry tests would have caught it.
*Fix:* delete the second definition (`route_registry.py:545-549`).
*Verify:* `python -c "from switchyard_config.route_registry import ROUTE_REGISTRY; assert len(ROUTE_REGISTRY) == 8"` and `pytest` passes.

### ‼️ DISCOVERED (2026-09-03, user report): "Show generated file preview" always failed — done (2026-09-03)
  Clicking the button in Review & Save showed "Preview failed:
  unknown". The backend was fine (`GET /api/preview` returned 200 with
  the TOML) — `ConfigManager.preview()` was the one endpoint whose
  response lacked the `ok` field, and the frontend handler gates on
  `if (!r.ok)`, so `undefined` read as failure with no `error` to show.
  Pre-existing since the initial commit; only surfaced once the user
  exercised the button.
  *Fix:* `preview()` now returns `"ok": True` like every other gated
  endpoint (`configurator/state.py`).
  *Verify:* new pytest `test_preview_response_is_ok_and_carries_extras`
  (`tests/test_model_extras.py`); jsdom preview-flow checks in
  `ui_smoke.js` (stub `previewPayload` with ok/toml/env/paths). 49 pytest
  pass. Live after 0.2.5: `curl :8800/api/preview` → `ok: True`,
  extra_body present, TOML renders.

### ‼️ DISCOVERED (2026-09-03, user report): provider tokens entered in the web UI are lost on save — done (2026-09-03)
  In Kubernetes the save path only PATCHed `routes.toml` +
  `provider_meta.json` into the config ConfigMap; `routes.toml` carries
  just the env var NAME (`api_key_env`), never the value. The token
  existed only in the configurator's memory and the ephemeral draft
  (cleared on save), so it evaporated on pod restart — the runtime was
  meant to get tokens from pre-provisioned Secrets via the chart's
  `providers:` values, but anything entered purely through the UI went
  nowhere.
  *Fix (chart 0.2.6):* the configurator now upserts tokens into a
  UI-managed Secret (`<release>-tokens`, `kube.upsert_token_secret`;
  merge-only — keys are never removed, so deleting a provider leaves a
  harmless stale key). Both deployments load it via `envFrom`
  (`optional: true`): switchyard sees the tokens as env vars, and the
  configurator re-reads them after restarts so the provider forms
  repopulate. Stakater Reloader rolls switchyard on every save (the
  ConfigMap change), which also picks up token updates. The Secret is
  created by the configurator's API client, never rendered by the chart,
  so Helm owns nothing there. Gated by `configurator.tokenSecret.enabled`
  (default true; dig-hardened so `--reuse-values` upgrades from
  pre-0.2.6 releases don't nil-pointer — note `--reuse-values` generally
  skips new chart defaults, prefer `-f <extracted values>`).
  *Verify:* 7 new tests in `tests/test_token_secret.py` (upsert
  patch/create/no-op/error paths + save-flow token collection, skip
  rules, failure surfacing); 49 pytest pass; `helm lint`/`template` for
  enabled+disabled+legacy values. Live after 0.2.6: provider+token via
  API → save → Secret `switchyard-tokens` created with the token →
  reloader rolled switchyard (`TOKENTEST_API_KEY` in pod env) →
  configurator pod restart → token repopulated in `/api/state` via
  envFrom. Test provider and Secret removed afterwards.
  *Incident note:* verifying the live round-trip, a test provider was
  deleted by list index without re-checking the list first — it removed
  the user's `openteset` provider (created that day, details not
  recoverable from the cluster: CM overwritten, drafts cleared, pods
  rolled). Lesson: always re-read state immediately before
  index-addressed mutations.

### 1️⃣ Migrate remaining route types into the registry — mostly done (2026-09-02)
- [x] Create `RandomHandler` (validation + `to_toml`). — done (`route_registry.py:93`)
- [x] Create `LlmClassifierCapabilityHandler`. — done (`:130`)
- [x] Create `LlmClassifierEscalationHandler`. — done (`:205`)
- [x] Create `LlmClassifierCustomHandler`. — done (`:242`)
- [x] Create `StageRouterHandler`. — done (`:308`)
- [x] Create `AdvisorHandler`. — done (`:376`)
- [x] Create `CompositeHandler`. — done (`:460`)
- [ ] Register all handlers in `route_registry.ROUTE_REGISTRY`. — BLOCKED: registrations exist at `:531-541` but are shadowed by the duplicate dict (see ‼️ above).
- [x] Remove the large `if/elif` block from `generate_toml`. — done; builds a dict serialized with `tomli_w` (`switchyard_config/routes.py:534`).
- ~~Update any imports that referenced the old constants (`ROUTE_TYPES`, `ROUTE_TYPE_KEYS`).~~ — obsolete (2026-09-02): the constants remain the canonical route-type list and are legitimately still used by `models.py` and `configurator/state.py`.

### 2️⃣ Add `/debug/cycles` endpoint — half done (2026-09-02)
- [x] Implement FastAPI route returning the cycle list. — done as `GET /api/debug/cycles` (`configurator/app.py:78`).
- [ ] Document the endpoint in the README. — still missing.

### 3️⃣ Host‑resource exporter agent
- [ ] Add a metrics exporter that scrapes Switchyard metrics and adds CPU/GPU usage. — note (2026-09-02): there is no `agents/` directory; a natural home is `switchyard_config/` or `configurator/`.
- ~~Add a Prometheus scrape target for the exporter in `prometheus/prometheus.yml`.~~ — obsolete (2026-09-02): the `prometheus/` directory was removed with the docker-compose stack; the chart now relies on cluster Prometheus via its ServiceMonitor.

### 4️⃣ Implement local OpenRouter pricing cache — obsolete (2026-09-02)
The entire opencode/OpenRouter integration (config writing, pricing lookups, `switchyard_config/opencode.py`) was removed from the project at user request; the pricing cache has nothing to cache.
- ~~Add a cache module with `load_cache()`, `save_cache()`, `is_fresh()`.~~
- ~~Modify the configurator to use the cache and expose a `GET /api/pricing/refresh` endpoint.~~

### 5️⃣ Add a central logger
- [ ] Initialise `logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))` in a common module (e.g., `switchyard_config/__init__.py`).
- [ ] Replace `print`/bare exceptions with `logger.error(..., exc_info=True)` where appropriate.

### 6️⃣ Write `SECURITY.md` — done (2026-09-02)
- [x] Explain Docker socket risks and how to use Docker secrets or TLS API instead. — done (`SECURITY.md` §1).
- [x] Provide best‑practice recommendations for production deployments. — done.

### 7️⃣ Version‑bump script — obsolete as written (2026-09-02)
~~Create `scripts/bump_version.py` that updates `SWITCHYARD_VERSION` in the Dockerfile and `docker-compose.yml`.~~ — obsolete: `docker-compose.yml` no longer exists after the Helm conversion.
- [ ] Replacement: `scripts/bump_version.py` that updates `SWITCHYARD_VERSION` (`switchyard/Dockerfile:13`), `appVersion` (`chart/Chart.yaml:6`), and the default `switchyard.image` tag in `chart/values.yaml` together.
- [ ] Add a git commit helper that tags the commit.

### 8️⃣ Extend CI pipeline
- [ ] Add a `lint` job (`ruff check .`).
- [ ] Add a `test` job (`pytest`).
- [ ] Ensure the pipeline fails on lint or test errors.

### 9️⃣ Write a minimal test suite — partial (2026-09-02)
- [x] Tests for each validator raising `ValidationError`. — done (`tests/test_validators.py`).
- [ ] Tests for `PassthroughHandler.validate` and `to_toml`.
- [ ] Tests for `ConfigManager._state_with_passthrough`.
- [ ] Tests for registry lookup and error handling. — would have caught the duplicate `ROUTE_REGISTRY` (see ‼️); write it against all 8 route types.

### 🔟 Update documentation — partial (2026-09-02)
- [x] Add an “Error handling” section to the README (describe `{field, error}` JSON format). — done (`README.md:53-55`).
- [ ] Document the `/api/debug/cycles` endpoint. — ~~`/api/pricing/refresh`~~ removed from this item (2026-09-02): that endpoint was never implemented and its rationale died with the opencode/OpenRouter removal (see 4️⃣).
- [x] Mention the auto‑generated passthrough routes in the Quick‑Start notes (already added).