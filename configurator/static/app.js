/* NeMo Switchyard web configurator - frontend logic. */
"use strict";

const S = { state: null, tab: "overview" };
// UI‑level configuration constants
const UI_CONFIG = {
  SLIDER_MAX: 20,
  SLIDER_STEP: 0.5,
};

const ROUTE_TYPE_DESCRIPTIONS = {
  passthrough:
    "Direct to a single target. The route id is the upstream model name clients send (e.g. GLM-5.3). No routing logic - just forwards the request through switchyard's metrics and translation layer.",
  random:
    "Weighted random selection across multiple targets. Weights are relative and do not need to sum to one. Useful for A/B testing, load balancing, or canary deployments. Omit weights for equal weighting.",
  llm_classifier_capability:
    "A judge model scores each task's difficulty and routes to a weak or strong target based on a threshold. Anything the judge cannot decide routes to the strong target. Good for cost optimization.",
  llm_classifier_escalation:
    "Like capability, but escalates to the strong target after N judge confirmations within a turn window. Keeps sessions on the weak model until it's clearly struggling, then upgrades for the rest of the session.",
  llm_classifier_custom:
    "Custom-mode classifier: the judge returns JSON matching a schema you supply, and a policy reads a selector from the verdict to pick one of several named targets. Good for routing across more than two tiers.",
  stage_router:
    "Scores tool-result and agent-progress signals from recent turns to pick a capable or efficient tier per turn, without an extra classifier call on every turn. Good for agentic workloads.",
  advisor:
    "An executor target runs, then an advisor reviews its output; gates further execution after max reviews or stall turns. The advisor is judge-only and never a routing destination.",
  composite:
    "Composes an LLM classifier with a stage router: the classifier picks the tier per user turn (or once per session), and the stage router runs the tool-execution loop on its own signals until the next user turn.",
};

const ROUTE_TYPES = [
  ["passthrough", "Passthrough"],
  ["random", "Random (A/B Split)"],
  ["llm_classifier_capability", "LLM Classifier - Capability"],
  ["llm_classifier_escalation", "LLM Classifier - Escalation"],
  ["llm_classifier_custom", "LLM Classifier - Custom"],
  ["stage_router", "Stage Router"],
  ["advisor", "Advisor Gate"],
  ["composite", "Composite (Classifier -> Stage)"],
];

const SECTION_FOR_TYPE = {
  passthrough: ["passthrough"],
  random: ["random"],
  llm_classifier_capability: ["llm_classifier_capability"],
  llm_classifier_escalation: ["llm_classifier_escalation"],
  llm_classifier_custom: ["llm_classifier_custom"],
  stage_router: ["stage_router"],
  advisor: ["advisor"],
  composite: ["composite"],
};

// ---------------------------------------------------------------------------
// Fetch helpers
// ---------------------------------------------------------------------------

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) {
    init.method = "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const resp = await fetch(path, init);
  const data = await resp.json().catch(() => ({ ok: false, error: `HTTP ${resp.status}` }));
  if (!resp.ok && !("ok" in data)) return { ok: false, error: `HTTP ${resp.status}` };
  return data;
}

function toast(title, body = "", severity = "warn") {
  const el = document.createElement("div");
  el.className = `toast ${severity}`;
  el.innerHTML = `<div class="t-title">${escapeHtml(title)}</div>${
    body ? `<div class="t-body">${escapeHtml(body)}</div>` : ""
  }`;
  document.getElementById("toasts").appendChild(el);
  setTimeout(() => el.remove(), 6000);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function load() {
  const s = await api("/api/state");
  if (!s.ok && s.error) { toast("Failed to load config", s.error, "err"); return; }
  S.state = s;
  renderStatus();
  renderTab(S.tab);
}

function renderStatus() {
  const pill = document.getElementById("status-pill");
  const unsaved = document.getElementById("unsaved-pill");
  api("/api/status").then((st) => {
    if (st.switchyard_running) {
      pill.textContent = "switchyard running";
      pill.className = "pill ok";
    } else {
      pill.textContent = "switchyard not running";
      pill.className = "pill bad";
    }
    if (st.has_unsaved_changes) {
      unsaved.classList.remove("hidden");
    } else {
      unsaved.classList.add("hidden");
    }
  }).catch(() => {
    pill.textContent = "status unavailable";
    pill.className = "pill";
  });
  if (S.state && S.state.has_unsaved_changes) unsaved.classList.remove("hidden");
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

function setTab(name) {
  S.tab = name;
  document.querySelectorAll(".tab").forEach((t) => {
    const isActive = t.dataset.tab === name;
    t.classList.toggle("active", isActive);
    t.setAttribute("aria-selected", isActive ? "true" : "false");
  });
  document.querySelectorAll(".pane").forEach((p) => p.classList.toggle("active", p.id === `pane-${name}`));
  renderTab(name);
  window.scrollTo({ top: 0 });
}

function renderTab(name) {
  if (!S.state) return;
  if (name === "providers") renderProviders();
  else if (name === "routes") renderRoutes();
  else if (name === "review") renderReview();
  else renderOverview();
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------

function renderOverview() {
  const st = S.state;
  const visibleProviders = st.providers.filter((p) => !p.is_self);
  const notes = [];
  if (st.draft_recovered) notes.push("<strong>Recovered unsaved changes</strong> from a previous session - save them on the Review screen to commit.");
  if (st.load_errors && st.load_errors.length)
    notes.push(`<strong>Config parse error:</strong> ${escapeHtml(st.load_errors.join("; "))}. Starting with an empty config is not applied - resolve the file or it will be overwritten on save.`);
  if (st.broken_route_indices && st.broken_route_indices.length) {
    const names = st.broken_route_indices.map((i) => escapeHtml(st.routes[i].name)).join(", ");
    notes.push(`<strong>${st.broken_route_indices.length} route(s) reference missing models:</strong> ${names}. Edit them before saving.`);
  }

  const el = document.getElementById("pane-overview");
  el.innerHTML = `
    <h2 class="section">Welcome</h2>
    <p class="sub">This page configures the NeMo Switchyard compose stack: add LLM providers (probed automatically),
    build routes, then review &amp; save <code>routes.toml</code> and <code>.env</code>.</p>
    ${notes.length ? `<div class="card" style="border-color:var(--warn)">${notes.map((n) => `<p style="margin:4px 0">${n}</p>`).join("")}</div>` : ""}
    <div class="card">
      <h2 class="section">Status</h2>
<div class="table-wrapper"><table>
  <tr><td style="width:200px">Providers</td><td><b>${visibleProviders.length}</b> configured</td></tr>
  <tr><td>Selected models</td><td><b>${st.selected_models.length}</b></td></tr>
  <tr><td>Custom routes</td><td><b>${st.custom_route_indices.length}</b> (+ ${st.passthrough_count} auto-passthrough)</td></tr>
  <tr><td>Files</td><td class="mono">${escapeHtml(st.files.routes)}<br>${escapeHtml(st.files.env)}</td></tr>
</table></div>
    </div>
    <div style="display:flex; gap:10px; flex-wrap:wrap">
      <button class="btn primary" id="go-providers">${visibleProviders.length ? "Edit Providers" : "Add Providers"}</button>
      <button class="btn" id="go-routes">Configure Routes</button>
      <button class="btn" id="go-review">Review &amp; Save</button>
    </div>
  `;
  el.querySelector("#go-providers").onclick = () => setTab("providers");
  el.querySelector("#go-routes").onclick = () => setTab("routes");
  el.querySelector("#go-review").onclick = () => setTab("review");
}

// ---------------------------------------------------------------------------
// Providers tab
// ---------------------------------------------------------------------------

function renderProviders() {
  const st = S.state;
  const visible = st.providers.filter((p) => !p.is_self);
  let html = `<h2 class="section">Step 1: LLM Providers</h2>
    <p class="sub">OpenAI-compatible LLM endpoints (OpenAI, Ollama, LM Studio, vLLM, ...). Each is probed and its model
    list fetched automatically. Use <code>host.docker.internal</code> to reach services running on the host machine.</p>`;
  if (st.broken_route_indices && st.broken_route_indices.length) {
    html += `<p class="card" style="border-color:var(--danger);color:var(--danger)">Some routes reference models that are no longer selected - fix or delete them on the Routes tab before saving.</p>`;
  }
  if (!visible.length) {
    html += `<div class="empty">No providers yet - click "Add Provider" below.</div>`;
  } else {
    html += `<div class="table-wrapper"><table>
      <tr><th>Provider</th><th>Endpoint</th><th>Models</th><th style="text-align:right">Actions</th></tr>`;
    let idx = 0;
    for (const p of st.providers) {
      if (p.is_self) continue;
      html += `<tr>
        <td><b>${escapeHtml(p.display_label)}</b> <span class="tag">${escapeHtml(p.name)}</span></td>
        <td class="mono">${escapeHtml(p.endpoint)}</td>
        <td><span class="tag ok">${p.selected_models.length} selected</span></td>
        <td style="text-align:right">
          <button class="btn small" data-edit-provider="${idx}">Edit</button>
          <button class="btn small danger" data-del-provider="${idx}">Delete</button>
        </td>
      </tr>`;
      idx++;
    }
    html += `</table></div>`;
  }
  html += `<div style="display:flex; gap:10px; margin-top:16px">
    <button class="btn primary" id="add-provider">Add Provider</button>
    <button class="btn" id="refresh-all">Refresh Models (all)</button>
  </div>`;
  const el = document.getElementById("pane-providers");
  el.innerHTML = html;

  el.querySelector("#add-provider").onclick = () => openProviderModal(null, -1);
  const ref = el.querySelector("#refresh-all");
  ref.onclick = async () => {
    ref.disabled = true; ref.textContent = "Refreshing...";
    const r = await api("/api/providers/refresh");
    ref.disabled = false; ref.textContent = "Refresh Models (all)";
    if (!r.ok) { toast("Refresh failed", r.error, "err"); return; }
    applyState(r);
    renderProviders();
    if (r.errors && r.errors.length) toast("Some providers failed", r.errors.join("\n"), "warn");
    else toast("Models refreshed", r.providers.join("\n"), "ok");
  };
  el.querySelectorAll("[data-edit-provider]").forEach((b) =>
    b.onclick = () => {
      const i = parseInt(b.dataset.editProvider, 10);
      openProviderModal(stateProviderIndex(i), i);
    });
  el.querySelectorAll("[data-del-provider]").forEach((b) =>
    b.onclick = async () => {
      const i = parseInt(b.dataset.delProvider, 10);
      if (!confirm("Delete this provider and its auto-passthrough routes?")) return;
      const r = await api(`/api/providers/${i}`, { method: "DELETE" });
      if (!r.ok) { toast("Delete failed", r.error, "err"); return; }
      applyState(r); renderProviders();
    });
}

// Map displayed provider row index -> index in state.providers (skips self provider).
function stateProviderIndex(displayIdx) {
  let d = -1;
  for (let i = 0; i < S.state.providers.length; i++) {
    if (S.state.providers[i].is_self) continue;
    d++;
    if (d === displayIdx) return i;
  }
  return -1;
}

// ---------------------------------------------------------------------------
// Provider modal
// ---------------------------------------------------------------------------

let PROV = { editingIdx: -1, available: [], selected: [], probedUrl: null };

function openProviderModal(provider, index) {
  PROV = {
    editingIdx: index,
    available: provider ? [...provider.available_models] : [],
    selected: provider ? [...provider.selected_models] : [],
  };
  document.getElementById("provider-modal-title").textContent =
    index >= 0 ? "Edit Provider" : "Add Provider";
  const p = index >= 0 && S.state ? S.state.providers[index] : null;
  document.getElementById("p-name").value = p ? p.name : "";
  document.getElementById("p-display").value = p ? p.display_name : "";
  document.getElementById("p-endpoint").value = p ? p.endpoint : "";
  document.getElementById("p-apikey").value = p ? p.api_key : "";
  document.getElementById("p-envvar").value = p ? p.api_key_env : "";
  document.getElementById("p-retries").value = p ? p.max_retries : 2;
  const status = document.getElementById("p-probe-status");
  status.textContent = ""; status.className = "probe-status";
  renderModelChecklist();
  showModal("provider-modal");
}

function renderModelChecklist() {
  const section = document.getElementById("p-models-section");
  const box = document.getElementById("p-models");
  box.innerHTML = "";
  if (!PROV.available.length) { section.classList.add("hidden"); return; }
  section.classList.remove("hidden");
  document.getElementById("p-model-count").textContent = `(${PROV.selected.length}/${PROV.available.length} selected)`;
  for (const m of PROV.available) {
    const l = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = PROV.selected.includes(m);
    cb.onchange = () => {
      if (cb.checked && !PROV.selected.includes(m)) PROV.selected.push(m);
      else PROV.selected = PROV.selected.filter((x) => x !== m);
      document.getElementById("p-model-count").textContent = `(${PROV.selected.length}/${PROV.available.length} selected)`;
    };
    l.appendChild(cb); l.appendChild(document.createTextNode(" " + m));
    box.appendChild(l);
  }
  document.getElementById("p-select-all").onclick = (e) => {
    e.preventDefault(); PROV.selected = [...PROV.available]; renderModelChecklist();
  };
  document.getElementById("p-select-none").onclick = (e) => {
    e.preventDefault(); PROV.selected = []; renderModelChecklist();
  };
}

function providerPayload() {
  return {
    name: document.getElementById("p-name").value.trim(),
    display_name: document.getElementById("p-display").value.trim(),
    endpoint: document.getElementById("p-endpoint").value.trim(),
    api_key: document.getElementById("p-apikey").value.trim(),
    api_key_env: document.getElementById("p-envvar").value.trim(),
    max_retries: parseInt(document.getElementById("p-retries").value, 10) || 0,
    available_models: PROV.available,
    selected_models: PROV.selected,
  };
}

function wireProviderModal() {
  document.getElementById("p-probe").onclick = async () => {
    const endpoint = document.getElementById("p-endpoint").value.trim();
    const apiKey = document.getElementById("p-apikey").value.trim();
    const status = document.getElementById("p-probe-status");
    if (!endpoint) { status.textContent = "Endpoint is required"; status.className = "probe-status err"; return; }
    status.textContent = "Probing endpoint (trying http/https and common ports)...";
    status.className = "probe-status";
    const r = await api("/api/providers/probe", { body: { endpoint, api_key: apiKey } });
    if (!r.ok) {
      status.textContent = "Error: " + r.error;
      status.className = "probe-status err";
      return;
    }
    document.getElementById("p-endpoint").value = r.url;
    PROV.available = r.models; PROV.selected = [...r.models];
    status.textContent = `Found ${r.models.length} models (all selected). Uncheck any you don't want.`;
    status.className = "probe-status ok";
    renderModelChecklist();
  };
  document.getElementById("p-refresh").onclick = async () => {
    const r = await api("/api/providers/refresh");
    if (!r.ok) { toast("Refresh failed", r.error, "err"); return; }
    applyState(r);
    const p = PROV.editingIdx >= 0 && S.state ? S.state.providers[PROV.editingIdx] : null;
    if (p) { PROV.available = [...p.available_models]; PROV.selected = [...p.selected_models]; }
    renderModelChecklist();
    toast("Models refreshed", (r.providers || []).join("\n") + (r.errors && r.errors.length ? "\nErrors:\n" + r.errors.join("\n") : ""), r.errors && r.errors.length ? "warn" : "ok");
  };
  document.getElementById("p-manual-add").onclick = () => {
    const inp = document.getElementById("p-manual");
    const added = [];
    for (const m of inp.value.split(",")) {
      const t = m.trim();
      if (!t) continue;
      if (!PROV.available.includes(t)) PROV.available.push(t);
      if (!PROV.selected.includes(t)) { PROV.selected.push(t); added.push(t); }
    }
    inp.value = "";
    renderModelChecklist();
    if (added.length) toast("Added models", added.join(", "), "ok");
  };
  document.getElementById("p-manual").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); document.getElementById("p-manual-add").click(); }
  });
document.getElementById("p-key-toggle").onclick = () => {
  const inp = document.getElementById("p-apikey");
  const showing = inp.type === "password";
  inp.type = showing ? "text" : "password";
  const btn = document.getElementById("p-key-toggle");
  btn.setAttribute("aria-label", showing ? "Hide API key" : "Show API key");
};
  document.getElementById("p-save").onclick = async () => {
    const body = { provider: providerPayload() };
    const r = PROV.editingIdx >= 0
      ? await api(`/api/providers/${PROV.editingIdx}`, { method: "PUT", body })
      : await api("/api/providers", { body });
    if (!r.ok) { toast("Validation", r.error, "err"); return; }
    applyState(r);
    closeModal("provider-modal");
    renderProviders();
    toast(r.notification || "Provider saved", "", "ok");
  };
}

// ---------------------------------------------------------------------------
// Routes tab
// ---------------------------------------------------------------------------

// Collapsible, per-provider chip list of available models. Keeps the routes
// tab compact when there are many models — the summary line shows the count,
// and the <details> expands to show each provider's models grouped together.
function renderModelLegend(providers) {
  const chips = (models) =>
    models
      .map((m) => `<span class="chip">${escapeHtml(m)}</span>`)
      .join("");
  const blocks = providers
    .map((p) => {
      const n = p.selected_models.length;
      if (!n) return "";
      return `<div class="legend-group">
        <div class="legend-head">
          <span class="legend-label">${escapeHtml(p.display_label || p.name)}</span>
          <span class="tag">${n}</span>
        </div>
        <div class="chip-row">${chips(p.selected_models)}</div>
      </div>`;
    })
    .filter(Boolean)
    .join("");
  if (!blocks) return "";
  return `<details class="legend">
    <summary>Show available models</summary>
    ${blocks}
  </details>`;
}

function renderRoutes() {
  const st = S.state;
  const visibleProviders = st.providers.filter((p) => !p.is_self);
  const modelCount = st.selected_models.length;
  const provCount = visibleProviders.length;
  const summary = modelCount
    ? `${modelCount} available model${modelCount === 1 ? "" : "s"} across ${provCount} provider${provCount === 1 ? "" : "s"}`
    : "(no models available — add a provider on the Providers tab)";
  let html = `<h2 class="section">Step 2: Route Configuration</h2>
    <p class="sub">${summary}</p>
    ${modelCount ? renderModelLegend(visibleProviders) : ""}
    <p class="help">${st.passthrough_count} auto-passthrough route(s) hidden - one per selected model.</p>`;
  const custom = st.custom_route_indices.map((i) => ({ idx: i, r: st.routes[i] }));
  if (!custom.length) {
    html += `<div class="empty">No custom routes - click "Add Route" to create one (passthrough, random, classifier, stage router, advisor, ...).</div>`;
  } else {
    html += `<div class="table-wrapper"><table>
      <tr><th>Name</th><th>Route ID</th><th>Type</th><th>Targets</th><th>Status</th><th style="text-align:right">Actions</th></tr>`;
    for (const { idx, r } of custom) {
      const broken = st.broken_route_indices.includes(idx);
      const missing = [];
      for (const m of (refsFor(r))) if (!st.selected_models.includes(m)) missing.push(m);
      html += `<tr>
        <td>${escapeHtml(r.name)}</td>
        <td class="mono">${escapeHtml(r.id)}</td>
        <td><span class="tag">${escapeHtml(r.type_label)}</span></td>
        <td class="mono">${escapeHtml(r.targets_summary)}</td>
        <td>${broken ? `<span class="tag err">missing: ${escapeHtml(missing.join(", "))}</span>` : `<span class="tag ok">OK</span>`}</td>
        <td style="text-align:right">
          <button class="btn small" data-edit-route="${idx}">Edit</button>
          <button class="btn small danger" data-del-route="${idx}">Delete</button>
        </td>
      </tr>`;
    }
    html += `</table></div>`;
  }
  html += `<div style="display:flex; gap:10px; margin-top:16px">
    <button class="btn primary" id="add-route">Add Route</button>
  </div>`;
  const el = document.getElementById("pane-routes");
  el.innerHTML = html;
  el.querySelector("#add-route").onclick = () => openRouteModal(null, -1);
  el.querySelectorAll("[data-edit-route]").forEach((b) =>
    b.onclick = () => { const i = parseInt(b.dataset.editRoute, 10); openRouteModal(S.state.routes[i], i); });
  el.querySelectorAll("[data-del-route]").forEach((b) =>
    b.onclick = async () => {
      const i = parseInt(b.dataset.delRoute, 10);
      if (!confirm("Delete this route?")) return;
      const r = await api(`/api/routes/${i}`, { method: "DELETE" });
      if (!r.ok) { toast("Delete failed", r.error, "err"); return; }
      applyState(r); renderRoutes();
    });
}

function refsFor(r) {
  switch (r.type) {
    case "passthrough": return r.target ? [r.target] : [];
    case "random": return r.targets;
    case "llm_classifier_custom":
      return [r.classifier_target, ...(r.targets || []), r.default_target].filter(Boolean);
    case "composite":
      return [r.stage_classifier_target, r.capable_target, r.efficient_target].filter(Boolean);
    default: {
      const keys = ["classifier_target", "weak_target", "strong_target", "capable_target", "efficient_target", "executor_target", "advisor_target"];
      return keys.map((k) => r[k]).filter(Boolean);
    }
  }
}

// ---------------------------------------------------------------------------
// Route modal
// ---------------------------------------------------------------------------

let ROUTE = { editingIdx: -1, type: "passthrough", randomWeights: {} };

function openRouteModal(route, index) {
  ROUTE = { editingIdx: index, type: route ? route.type : "passthrough" };
  document.getElementById("route-modal-title").textContent =
    index >= 0 ? "Edit Route" : "Add Route";
  document.getElementById("r-name").value = route ? route.name : "";
  document.getElementById("r-id").value = route ? route.id : "";
  const typeSel = document.getElementById("r-type");
  typeSel.innerHTML = ROUTE_TYPES.map(([k, l]) => `<option value="${k}">${escapeHtml(l)}</option>`).join("");
  typeSel.value = ROUTE.type;
  typeSel.onchange = () => { ROUTE.type = typeSel.value; renderRouteType(); };

  // Fill per-type fields from the route (or defaults)
  const v = (key, def) => (route && route[key]) || def;
  // passthrough
  setSelectVal("r-passthrough-target", v("target", ""), true);
  // random
  setRandomTargets((route ? route.targets : []), v("weights", ""));
  // llm_classifier_capability
  setSelectVal("r-cap-classifier", v("classifier_target", ""));
  setSelectVal("r-cap-weak", v("weak_target", ""));
  setSelectVal("r-cap-strong", v("strong_target", ""));
  setVal("r-cap-threshold", v("base_threshold", "0.5"));
  setVal("r-cap-threshold-step", v("threshold_step", "0.0"));
  setVal("r-cap-trigger", v("classify_trigger", "every_request"));
  setCheck("r-cap-hash-fallback", v("message_hash_fallback", "false"));
  setVal("r-cap-window", v("recent_turn_window", ""));
  setVal("r-cap-rft", v("response_format_type", "json_schema"));
  setVal("r-cap-max-tokens", v("max_output_tokens", "4096"));
  setVal("r-cap-prompt", v("prompt", ""));
  // llm_classifier_escalation
  setSelectVal("r-esc-judge", v("classifier_target", ""));
  setSelectVal("r-esc-weak", v("weak_target", ""));
  setSelectVal("r-esc-strong", v("strong_target", ""));
  setVal("r-esc-confirmations", v("confirmations", "2"));
  setVal("r-esc-window", v("recent_turn_window", "28"));
  setVal("r-esc-msg-chars", v("window_message_chars", "500"));
  setVal("r-esc-rft", v("response_format_type", "json_schema"));
  setVal("r-esc-max-tokens", v("max_output_tokens", "4096"));
  setVal("r-esc-prompt", v("prompt", ""));
  // llm_classifier_custom
  setSelectVal("r-cust-classifier", v("classifier_target", ""));
  setChecklist("r-cust-targets", (route ? route.targets : []));
  setSelectVal("r-cust-default", v("default_target", ""), true);
  setVal("r-cust-selector", v("policy_selector", ""));
  setVal("r-cust-schema", v("response_schema", ""));
  setVal("r-cust-trigger", v("classify_trigger", "every_request"));
  setVal("r-cust-rft", v("response_format_type", "json_schema"));
  setVal("r-cust-max-tokens", v("max_output_tokens", "4096"));
  setVal("r-cust-prompt", v("prompt", ""));
  // stage_router
  setSelectVal("r-stage-capable", v("capable_target", ""));
  setSelectVal("r-stage-efficient", v("efficient_target", ""));
  setVal("r-stage-picker", v("picker", "efficient_first"));
  setVal("r-stage-confidence", v("confidence_threshold", "0.5"));
  setVal("r-stage-window", v("recent_turn_window", ""));
  setVal("r-stage-cap-prompt", v("capable_system_prompt", ""));
  setVal("r-stage-eff-prompt", v("efficient_system_prompt", ""));
  setVal("r-stage-handoff-esc", v("handoff_escalation_note", ""));
  setVal("r-stage-handoff-deesc", v("handoff_deescalation_note", ""));
  setCheck("r-stage-handoff-only", v("handoff_only_on_wrong_signal_escalation", "true"));
  setCheck("r-stage-cls-enabled", v("stage_classifier_enabled", "false"));
  setSelectVal("r-stage-cls-target", v("stage_classifier_target", ""));
  setVal("r-stage-cls-threshold", v("stage_classifier_base_threshold", "0.5"));
  setVal("r-stage-cls-step", v("stage_classifier_threshold_step", "0.1"));
  setVal("r-stage-cls-window", v("stage_classifier_recent_turn_window", "3"));
  setVal("r-stage-cls-rft", v("stage_classifier_response_format_type", "json_schema"));
  setVal("r-stage-cls-prompt", v("stage_classifier_prompt", ""));
  // advisor
  setSelectVal("r-adv-executor", v("executor_target", ""));
  setSelectVal("r-adv-advisor", v("advisor_target", ""));
  setVal("r-adv-reviews", v("max_reviews", "3"));
  setVal("r-adv-stall", v("gate_stall_turns", "30"));
  setVal("r-adv-trigger", v("gate_trigger", "no_tool_call"));
  setVal("r-adv-trigger-pattern", v("gate_trigger_pattern", ""));
  setVal("r-adv-min-tool", v("gate_min_tool_results", "0"));
  setVal("r-adv-max-tokens", v("advisor_max_tokens", "2048"));
  setVal("r-adv-temp", v("advisor_temperature", ""));
  setVal("r-adv-transcript", v("transcript_max_chars", "200000"));
  setCheck("r-adv-fail-open", v("fail_open", "true"));
  setVal("r-adv-reviewer-prompt", v("reviewer_system_prompt", ""));
  setVal("r-adv-redo-prefix", v("redo_feedback_prefix", ""));
  // composite
  setSelectVal("r-comp-judge", v("stage_classifier_target", ""));
  setSelectVal("r-comp-capable", v("capable_target", ""));
  setSelectVal("r-comp-efficient", v("efficient_target", ""));
  setVal("r-comp-confidence", v("confidence_threshold", "0.5"));
  setVal("r-comp-trigger", v("classify_trigger", "user_turn"));
  setVal("r-comp-cls-threshold", v("stage_classifier_base_threshold", "0.5"));
  setVal("r-comp-cls-step", v("stage_classifier_threshold_step", "0.1"));
  setVal("r-comp-window", v("recent_turn_window", ""));
  setCheck("r-comp-hash-fallback", v("message_hash_fallback", "false"));
  setVal("r-comp-cap-prompt", v("capable_system_prompt", ""));
  setVal("r-comp-eff-prompt", v("efficient_system_prompt", ""));
  setVal("r-comp-handoff-esc", v("handoff_escalation_note", ""));
  setVal("r-comp-handoff-deesc", v("handoff_deescalation_note", ""));
  setCheck("r-comp-handoff-only", v("handoff_only_on_wrong_signal_escalation", "true"));

  document.getElementById("r-type-desc").textContent = ROUTE_TYPE_DESCRIPTIONS[ROUTE.type] || "";
  renderRouteType();
  showModal("route-modal");
}

function renderRouteType() {
  // Hide all sections except the one for the current type
  for (const sec of ["passthrough", "random", "llm_classifier_capability", "llm_classifier_escalation", "llm_classifier_custom", "stage_router", "advisor", "composite"]) {
    document.getElementById(`sec-${sec}`).classList.toggle("hidden", sec !== ROUTE.type);
  }
  document.getElementById("r-type-desc").textContent = ROUTE_TYPE_DESCRIPTIONS[ROUTE.type] || "";
  document.getElementById("r-type").value = ROUTE.type;
}

function modelOptions(current) {
  // Models usable as targets: all selected models except this route's own id (prevents self-loop).
  const ownId = document.getElementById("r-id").value.trim();
  const models = S.state.selected_models.filter((m) => m !== ownId);
  let opts = '';
  if (!models.length) return { any: false, options: `<option value="">(no models available)</option>` };
  if (!models.includes(current)) {
    opts += `<option value="">-- select a model --</option>`;
  }
  for (const m of models) {
    opts += `<option value="${escapeHtml(m)}" ${m === current ? "selected" : ""}>${escapeHtml(m)}</option>`;
  }
  if (current && !models.includes(current)) {
    opts += `<option value="${escapeHtml(current)}" selected>(missing) ${escapeHtml(current)}</option>`;
  }
  return { any: true, options: opts };
}

function setSelectVal(id, current, required) {
  const opts = modelOptions(current);
  const wrap = document.getElementById(id);
  if (!wrap) return;
  // Build the select element via DOM to attach an accessible label
+    const selectEl = document.createElement('select');
+    if (required) selectEl.className = 'mselect';
+    selectEl.innerHTML = opts.options;
+    // Attach an aria-label derived from the preceding <label> if present
+    const labelEl = wrap.previousElementSibling;
+    if (labelEl && labelEl.tagName.toLowerCase() === 'label') {
+      selectEl.setAttribute('aria-label', labelEl.textContent.trim());
+    }
+    wrap.innerHTML = '';
+    wrap.appendChild(selectEl);
+    // original line retained for reference (now replaced)
+    //wrap.innerHTML = `<select ${required ? 'class="mselect"' : ''}>${opts.options}</select>`;

  const sel = wrap.querySelector("select");
  if (!opts.any && current) {
    // Only option is a missing model - keep the select showing "(missing)".
    sel.value = current;
  }
}

function setChecklist(id, selected) {
  const wrap = document.getElementById(id);
  wrap.innerHTML = "";
  const models = S.state.selected_models.filter((m) => m !== document.getElementById("r-id").value.trim());
  if (!models.length) { wrap.innerHTML = `<p class="help">(no models available)</p>`; return; }
  for (const m of models) {
    const l = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.value = m; cb.checked = selected.includes(m);
    l.appendChild(cb); l.appendChild(document.createTextNode(" " + m));
    wrap.appendChild(l);
  }
}

// --- Random route: per-target weight sliders ---
// Replaces the comma-separated weights text input with a slider per checked
// target, showing the weight value and the live percentage of traffic each
// target receives. Weights are relative (don't need to sum to 1); a weight
// of 0 disables that target.
function setRandomTargets(selected, weightsStr) {
  const wrap = document.getElementById("r-random-targets");
  wrap.innerHTML = "";
  ROUTE.randomWeights = {};
  const ownId = document.getElementById("r-id").value.trim();
  const models = S.state.selected_models.filter((m) => m !== ownId);
  if (!models.length) {
    wrap.innerHTML = `<p class="help">(no models available)</p>`;
    return;
  }
  // Pair the positional weights string with the selected targets.
  const parts = (weightsStr || "").split(",").map((s) => s.trim()).filter(Boolean);
  selected.forEach((t, i) => { ROUTE.randomWeights[t] = parts[i] || "1"; });

  for (const m of models) {
    const isSel = selected.includes(m);
    const item = document.createElement("div");
    item.className = "rw-item";
    const row = document.createElement("label");
    row.className = "rw-row";
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.value = m; cb.checked = isSel;
    cb.onchange = () => {
      if (cb.checked && !ROUTE.randomWeights[m]) ROUTE.randomWeights[m] = "1";
      renderRandomWeights();
    };
    const name = document.createElement("span");
    name.className = "rw-name"; name.textContent = m;
    row.appendChild(cb); row.appendChild(name);
    const wc = document.createElement("div");
    wc.className = "rw-weight"; wc.dataset.model = m;
    item.appendChild(row); item.appendChild(wc);
    wrap.appendChild(item);
  }
  renderRandomWeights();
}

function renderRandomWeights() {
  const wrap = document.getElementById("r-random-targets");
  if (!wrap) return;
  const items = wrap.querySelectorAll(".rw-item");
  const checked = [];
  items.forEach((item) => {
    const cb = item.querySelector("input[type=checkbox]");
    if (cb.checked) checked.push(cb.value);
  });
  const vals = checked.map((m) => parseFloat(ROUTE.randomWeights[m] || "1") || 0);
  const total = vals.reduce((a, b) => a + b, 0);

  items.forEach((item) => {
    const cb = item.querySelector("input[type=checkbox]");
    const m = cb.value;
    const wc = item.querySelector(".rw-weight");
    const visible = cb.checked;
    wc.style.display = visible ? "flex" : "none";
    if (!visible) return;

    // Build the slider once per row, then just update values on re-render.
    let slider = wc.querySelector("input[type=range]");
    if (!slider) {
      slider = document.createElement("input");
      slider.type = "range"; slider.min = "0"; slider.max = UI_CONFIG.SLIDER_MAX; slider.step = UI_CONFIG.SLIDER_STEP;
      slider.oninput = () => {
        ROUTE.randomWeights[m] = slider.value;
        renderRandomWeights();
      };
      const num = document.createElement("span");
      num.className = "rw-num";
      const pct = document.createElement("span");
      pct.className = "rw-pct";
      wc.appendChild(slider); wc.appendChild(num); wc.appendChild(pct);
    }
    const w = parseFloat(ROUTE.randomWeights[m] || "1") || 0;
    // Don't clobber the slider position while the user is dragging it.
    if (Math.abs(parseFloat(slider.value) - w) > 0.01) slider.value = w;
    wc.querySelector(".rw-num").textContent = w.toFixed(1);
    const pct = total > 0 ? (w / total) * 100 : 0;
    wc.querySelector(".rw-pct").textContent = total > 0 ? pct.toFixed(0) + "%" : "\u2014";
  });
}

function randomWeightsString() {
  const wrap = document.getElementById("r-random-targets");
  if (!wrap) return "";
  const checked = Array.from(wrap.querySelectorAll(".rw-item input[type=checkbox]:checked")).map((c) => c.value);
  return checked.map((m) => ROUTE.randomWeights[m] || "1").join(", ");
}

// Set an input/textarea/select value, tolerating a missing element (so a
// field that doesn't exist for the current route type is a no-op).
function setVal(id, val) {
  const el = document.getElementById(id);
  if (el) el.value = val || "";
}

// Check a checkbox from a form-string bool ("true"/"false"/""/...).
function setCheck(id, val) {
  const el = document.getElementById(id);
  if (el) el.checked = /^(true|1|yes|on)$/i.test(String(val || "").trim());
}

// Read an input/textarea/select value ("" if the element is absent).
function getVal(id) {
  const el = document.getElementById(id);
  return el ? el.value.trim() : "";
}

// Read a checkbox as a "true"/"false" string.
function getCheck(id) {
  const el = document.getElementById(id);
  return el && el.checked ? "true" : "false";
}

function routePayload() {
  const type = ROUTE.type;
  const p = {
    name: document.getElementById("r-name").value.trim(),
    id: document.getElementById("r-id").value.trim(),
    type,
  };
  const selVal = (wrapId) => (document.getElementById(wrapId).querySelector("select") || {}).value || "";
  const checked = (wrapId) => Array.from(document.querySelectorAll(`#${wrapId} input:checked`)).map((c) => c.value);
  switch (type) {
    case "passthrough":
      p.target = selVal("r-passthrough-target");
      break;
    case "random":
      p.targets = checked("r-random-targets");
      p.weights = randomWeightsString();
      break;
    case "llm_classifier_capability":
      p.classifier_target = selVal("r-cap-classifier");
      p.weak_target = selVal("r-cap-weak");
      p.strong_target = selVal("r-cap-strong");
      p.base_threshold = getVal("r-cap-threshold");
      p.threshold_step = getVal("r-cap-threshold-step");
      p.classify_trigger = getVal("r-cap-trigger");
      p.message_hash_fallback = getCheck("r-cap-hash-fallback");
      p.recent_turn_window = getVal("r-cap-window");
      p.response_format_type = getVal("r-cap-rft");
      p.max_output_tokens = getVal("r-cap-max-tokens");
      p.prompt = getVal("r-cap-prompt");
      break;
    case "llm_classifier_escalation":
      p.classifier_target = selVal("r-esc-judge");
      p.weak_target = selVal("r-esc-weak");
      p.strong_target = selVal("r-esc-strong");
      p.confirmations = getVal("r-esc-confirmations");
      p.recent_turn_window = getVal("r-esc-window");
      p.window_message_chars = getVal("r-esc-msg-chars");
      p.response_format_type = getVal("r-esc-rft");
      p.max_output_tokens = getVal("r-esc-max-tokens");
      p.prompt = getVal("r-esc-prompt");
      break;
    case "llm_classifier_custom":
      p.classifier_target = selVal("r-cust-classifier");
      p.targets = checked("r-cust-targets");
      p.default_target = selVal("r-cust-default");
      p.policy_type = "target_selector";
      p.policy_selector = getVal("r-cust-selector");
      p.response_schema = getVal("r-cust-schema");
      p.classify_trigger = getVal("r-cust-trigger");
      p.response_format_type = getVal("r-cust-rft");
      p.max_output_tokens = getVal("r-cust-max-tokens");
      p.prompt = getVal("r-cust-prompt");
      break;
    case "stage_router":
      p.capable_target = selVal("r-stage-capable");
      p.efficient_target = selVal("r-stage-efficient");
      p.picker = getVal("r-stage-picker");
      p.confidence_threshold = getVal("r-stage-confidence");
      p.recent_turn_window = getVal("r-stage-window");
      p.capable_system_prompt = getVal("r-stage-cap-prompt");
      p.efficient_system_prompt = getVal("r-stage-eff-prompt");
      p.handoff_escalation_note = getVal("r-stage-handoff-esc");
      p.handoff_deescalation_note = getVal("r-stage-handoff-deesc");
      p.handoff_only_on_wrong_signal_escalation = getCheck("r-stage-handoff-only");
      p.stage_classifier_enabled = getCheck("r-stage-cls-enabled");
      p.stage_classifier_target = selVal("r-stage-cls-target");
      p.stage_classifier_base_threshold = getVal("r-stage-cls-threshold");
      p.stage_classifier_threshold_step = getVal("r-stage-cls-step");
      p.stage_classifier_recent_turn_window = getVal("r-stage-cls-window");
      p.stage_classifier_response_format_type = getVal("r-stage-cls-rft");
      p.stage_classifier_prompt = getVal("r-stage-cls-prompt");
      break;
    case "advisor":
      p.executor_target = selVal("r-adv-executor");
      p.advisor_target = selVal("r-adv-advisor");
      p.max_reviews = getVal("r-adv-reviews");
      p.gate_stall_turns = getVal("r-adv-stall");
      p.gate_trigger = getVal("r-adv-trigger");
      p.gate_trigger_pattern = getVal("r-adv-trigger-pattern");
      p.gate_min_tool_results = getVal("r-adv-min-tool");
      p.advisor_max_tokens = getVal("r-adv-max-tokens");
      p.advisor_temperature = getVal("r-adv-temp");
      p.transcript_max_chars = getVal("r-adv-transcript");
      p.fail_open = getCheck("r-adv-fail-open");
      p.reviewer_system_prompt = getVal("r-adv-reviewer-prompt");
      p.redo_feedback_prefix = getVal("r-adv-redo-prefix");
      break;
    case "composite":
      p.stage_classifier_target = selVal("r-comp-judge");
      p.capable_target = selVal("r-comp-capable");
      p.efficient_target = selVal("r-comp-efficient");
      p.confidence_threshold = getVal("r-comp-confidence");
      p.classify_trigger = getVal("r-comp-trigger");
      p.stage_classifier_base_threshold = getVal("r-comp-cls-threshold");
      p.stage_classifier_threshold_step = getVal("r-comp-cls-step");
      p.recent_turn_window = getVal("r-comp-window");
      p.message_hash_fallback = getCheck("r-comp-hash-fallback");
      p.capable_system_prompt = getVal("r-comp-cap-prompt");
      p.efficient_system_prompt = getVal("r-comp-eff-prompt");
      p.handoff_escalation_note = getVal("r-comp-handoff-esc");
      p.handoff_deescalation_note = getVal("r-comp-handoff-deesc");
      p.handoff_only_on_wrong_signal_escalation = getCheck("r-comp-handoff-only");
      break;
  }
  return p;
}

function wireRouteModal() {
  document.getElementById("r-id").addEventListener("focus", () => {
    const id = document.getElementById("r-id").value.trim();
    const name = document.getElementById("r-name").value.trim();
    if (!id && name) document.getElementById("r-id").value = `switchyard/${name}`;
  });
  document.getElementById("r-save").onclick = async () => {
    const body = { route: routePayload() };
    const r = ROUTE.editingIdx >= 0
      ? await api(`/api/routes/${ROUTE.editingIdx}`, { method: "PUT", body })
      : await api("/api/routes", { body });
    if (!r.ok) { toast("Validation", r.error, "err"); return; }
    applyState(r);
    closeModal("route-modal");
    renderRoutes();
    toast(r.notification || "Route saved", "", "ok");
  };
}

// ---------------------------------------------------------------------------
// Review & Save
// ---------------------------------------------------------------------------

async function renderReview() {
  const st = S.state;
  const el = document.getElementById("pane-review");
  const visible = st.providers.filter((p) => !p.is_self);
  let provLines = visible.map((p) =>
    `${escapeHtml(p.display_label)} [${escapeHtml(p.name)}] ${escapeHtml(p.endpoint)} - key: ${p.api_key ? "****(set)" : "(not set)"} - ${p.selected_models.length} models`);
  if (!provLines.length) provLines = ["(none)"];
  const custom = st.custom_route_indices.map((i) => ({ idx: i, r: st.routes[i] }));
  let routeRows = custom.map(({ idx, r }) => {
    const broken = st.broken_route_indices.includes(idx);
    return `<tr>
      <td>${escapeHtml(r.id)}</td><td>${escapeHtml(r.name)}</td>
      <td><span class="tag">${escapeHtml(r.type_label)}</span></td>
      <td class="mono">${escapeHtml(r.targets_summary)}</td>
      <td>${broken ? `<span class="tag err">missing models</span>` : `<span class="tag ok">OK</span>`}</td>
    </tr>`;
  }).join("");

  el.innerHTML = `
    <h2 class="section">Step 3: Review &amp; Save</h2>
    <div class="card">
      <h2 class="section">Providers (${visible.length})</h2>
      ${provLines.map((l) => `<p class="help" style="margin:2px 0">${l}</p>`).join("")}
      ${st.providers.some((p) => p.is_self) ? `<p class="help">[self] synthetic provider for route chaining (${st.providers.find((p) => p.is_self).selected_models.length} route(s))</p>` : ""}
      <p class="sub" style="margin-top:10px">Selected models: <b>${st.selected_models.length}</b></p>
    </div>
    <div class="card">
      <h2 class="section">Routes (${custom.length} custom + ${st.passthrough_count} auto-passthrough)</h2>
      ${custom.length ? `<div class=\"table-wrapper\"><table><tr><th>Route ID</th><th>Name</th><th>Type</th><th>Targets</th><th>Status</th></tr>${routeRows}</table></div>` : `<div class=\"empty\">(none)</div>`}
    </div>
    <div class="card">
      <h2 class="section">Config files</h2>
      <p class="help">Files written on save (previous versions are backed up with a timestamp):</p>
      <p class="help mono">${escapeHtml(st.files.routes)}<br>${escapeHtml(st.files.env)}<br>${escapeHtml(st.files.meta)}</p>
      <div id="save-status"></div>
      <div style="display:flex; gap:10px; margin-top:14px; flex-wrap:wrap">
        <button class="btn success" id="do-save">Save Configuration</button>
        <button class="btn" id="do-restart">Restart switchyard</button>
        <button class="btn ghost" id="do-preview">Show generated file preview</button>
      </div>
    </div>
    <div class="card hidden" id="preview-card"></div>
  `;

  el.querySelector("#do-save").onclick = async () => {
    const btn = el.querySelector("#do-save");
    btn.disabled = true; btn.textContent = "Saving...";
    const r = await api("/api/save", { method: "POST", body: {} });
    btn.disabled = false; btn.textContent = "Save Configuration";
    const statusBox = el.querySelector("#save-status");
    if (!r.ok) {
      statusBox.className = "statusline err";
      statusBox.textContent = (r.title ? r.title + ": " : "") + r.error;
      toast(r.title || "Validation", r.error, "err");
      return;
    }
    statusBox.className = "statusline ok";
    statusBox.textContent = r.message + "\n" + r.files.join("\n");
    applyState(r);
    document.getElementById("unsaved-pill").classList.add("hidden");
    renderStatus();
    toast(r.message, r.files.join("\n"), "ok");
    renderRoutes();
  };

  el.querySelector("#do-restart").onclick = async () => {
    const btn = el.querySelector("#do-restart");
    btn.disabled = true; btn.textContent = "Restarting...";
    const r = await api("/api/restart", { method: "POST", body: {} });
    btn.disabled = false; btn.textContent = "Restart switchyard";
    const statusBox = el.querySelector("#save-status");
    statusBox.className = r.ok ? "statusline ok" : "statusline err";
    statusBox.textContent = r.message;
    if (r.ok) toast("Switchyard restarted", "", "ok");
    else toast("Restart failed", r.message, "err");
    renderStatus();
  };

  el.querySelector("#do-preview").onclick = async () => {
    const card = el.querySelector("#preview-card");
    if (!card.classList.contains("hidden")) { card.classList.add("hidden"); return; }
    card.classList.remove("hidden");
    card.innerHTML = `<div class="preview-head"><strong>Generated files</strong><span class="copy-hint">Copy-paste into the terminal, or save from the UI above.</span></div>`;
    const r = await api("/api/preview");
    if (!r.ok) { card.innerHTML = `<p class="statusline err">Preview failed: ${escapeHtml(r.error || "unknown")}</p>`; return; }
    const toml = document.createElement("div");
    toml.innerHTML = `<div class="preview-head"><strong>routes.toml</strong><button class="btn small" data-copy>Copy</button></div><textarea readonly></textarea>`;
    toml.querySelector("textarea").value = r.toml;
    toml.querySelector("[data-copy]").onclick = () => {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(r.toml).then(() => toast("Copied routes.toml", "", "ok"));
        } else {
          const ta = document.createElement('textarea');
          ta.value = r.toml;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          ta.remove();
          toast("Copied routes.toml (fallback)", "", "ok");
        }
      };
    const envwrap = document.createElement("div");
    envwrap.innerHTML = `<div class="preview-head"><strong>.env (updated API keys)</strong><button class="btn small" data-copy>Copy</button></div><textarea readonly style="min-height:180px"></textarea>`;
    envwrap.querySelector("textarea").value = r.env;
    envwrap.querySelector("[data-copy]").onclick = () => {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(r.env).then(() => toast("Copied .env", "", "ok"));
        } else {
          const ta = document.createElement('textarea');
          ta.value = r.env;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          ta.remove();
          toast("Copied .env (fallback)", "", "ok");
        }
      };
    card.appendChild(toml); card.appendChild(envwrap);
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function applyState(r) {
  if (r && r.state) {
    S.state = r.state;
    renderStatus();
  }
}

let previousFocus = null;

function trapFocus(modal) {
  const focusableSelectors = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';
  const focusable = modal.querySelectorAll(focusableSelectors);
  if (focusable.length === 0) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const handler = (e) => {
    if (e.key !== 'Tab') return;
    if (e.shiftKey) {
      if (document.activeElement === first) {
        e.preventDefault();
        last.focus();
      }
    } else {
      if (document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  };
  modal.addEventListener('keydown', handler);
  modal._focusHandler = handler;
}

function showModal(id) {
  const modal = document.getElementById(id);
  previousFocus = document.activeElement;
  modal.classList.remove('hidden');
  trapFocus(modal);
  // focus the first focusable element inside the modal
  const firstInput = modal.querySelector('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
  if (firstInput) firstInput.focus();
}

function closeModal(id) {
  const modal = document.getElementById(id);
  modal.classList.add('hidden');
  if (modal._focusHandler) {
    modal.removeEventListener('keydown', modal._focusHandler);
    delete modal._focusHandler;
  }
  if (previousFocus && typeof previousFocus.focus === 'function') {
    previousFocus.focus();
  }
}


// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

function init() {
  document.getElementById("tabs").addEventListener("click", (e) => {
    const tab = e.target.closest(".tab");
    if (tab) setTab(tab.dataset.tab);
  });
  document.querySelectorAll("[data-close]").forEach((b) =>
    b.addEventListener("click", () => closeModal(b.dataset.close)));
  document.querySelectorAll(".modal").forEach((m) =>
    m.addEventListener("mousedown", (e) => { if (e.target === m) closeModal(m.id); }));
document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") document.querySelectorAll(".modal").forEach((m) => closeModal(m.id));
    });
  wireProviderModal();
  wireRouteModal();
  load();
}

document.addEventListener("DOMContentLoaded", init);
