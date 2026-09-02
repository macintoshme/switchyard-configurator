# Project Next‑Step Checklist

The items are ordered by impact. After each step the repository should be **committed and tested** before moving to the next.

---

## 1️⃣ Migrate remaining route types into the registry
- [ ] Create `RandomHandler` (validation + `to_toml`).
- [ ] Create `LlmClassifierCapabilityHandler`.
- [ ] Create `LlmClassifierEscalationHandler`.
- [ ] Create `LlmClassifierCustomHandler`.
- [ ] Create `StageRouterHandler`.
- [ ] Create `AdvisorHandler`.
- [ ] Create `CompositeHandler`.
- [ ] Register all handlers in `route_registry.ROUTE_REGISTRY`.
- [ ] Remove the large `if/elif` block from `generate_toml` (keep only fallback for legacy types, if any).
- [ ] Update any imports that referenced the old constants (`ROUTE_TYPES`, `ROUTE_TYPE_KEYS`).

## 2️⃣ Add `/debug/cycles` endpoint
- [ ] Implement FastAPI route returning the cycle list.
- [ ] Document the endpoint in the README.

## 3️⃣ Host‑resource exporter agent
- [ ] Add `agents/metrics_exporter.py` that scrapes Switchyard metrics and adds CPU/GPU usage.
- [ ] Add a Prometheus scrape target for the exporter in `prometheus/prometheus.yml`.

## 4️⃣ Implement local OpenRouter pricing cache
- [ ] Add `agents/cache.py` with `load_cache()`, `save_cache()`, `is_fresh()`.
- [ ] Modify the configurator to use the cache and expose a `GET /api/pricing/refresh` endpoint.

## 5️⃣ Add a central logger
- [ ] Initialise `logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))` in a common module (e.g., `switchyard_config/__init__.py`).
- [ ] Replace `print`/bare exceptions with `logger.error(..., exc_info=True)` where appropriate.

## 6️⃣ Write `SECURITY.md`
- [ ] Explain Docker socket risks and how to use Docker secrets or TLS API instead.
- [ ] Provide best‑practice recommendations for production deployments.

## 7️⃣ Version‑bump script
- [ ] Create `scripts/bump_version.py` that updates `SWITCHYARD_VERSION` in the Dockerfile and `docker-compose.yml`.
- [ ] Add a git commit helper that tags the commit.

## 8️⃣ Extend CI pipeline
- [ ] Add a `lint` job (`ruff check .`).
- [ ] Add a `test` job (`pytest`).
- [ ] Ensure the pipeline fails on lint or test errors.

## 9️⃣ Write a minimal test suite
- [ ] Tests for each validator raising `ValidationError`.
- [ ] Tests for `PassthroughHandler.validate` and `to_toml`.
- [ ] Tests for `ConfigManager._state_with_passthrough`.
- [ ] Tests for registry lookup and error handling.

## 🔟 Update documentation
- [ ] Add an “Error handling” section to the README (describe `{field, error}` JSON format).
- [ ] Document the new `/debug/cycles` and `/api/pricing/refresh` endpoints.
- [ ] Mention the auto‑generated passthrough routes in the Quick‑Start notes (already added).

---

*Proceed with step 1 (RandomHandler) before moving on to the next items.*