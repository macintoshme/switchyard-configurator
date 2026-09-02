# Project Goals – Switchyard Configurator

## High‑impact objectives

1. **Refactor route‑type handling**
   - Introduce a registry/plugin architecture (`route_type -> handler`) that encapsulates TOML conversion, validation, and UI rendering.
   - New route types can be added without touching multiple modules.

2. **Cache OpenRouter pricing**
   - Store fetched pricing data locally (e.g. `~/.cache/switchyard/openrouter.json`).
   - Provide a CLI flag/command to force a refresh.
   - Reduce repeated network traffic on successive saves.

3. **Document and improve security**
   - Add a dedicated **SECURITY.md** explaining the implications of mounting the Docker socket and storing secrets in `.env`.
   - Offer alternative deployment guides (Docker secrets, Kubernetes Secrets, TLS‑secured Docker API).

4. **Optimize Rust build pipeline**
   - Use `cargo‑chef` to cache dependencies and speed up rebuilds.
   - Optionally provide a `scratch`‑based final image for minimal footprint.

5. **Expose debugging endpoint**
   - Add a simple HTTP endpoint (e.g. `/debug/cycles`) that returns any detected route cycles.
   - Helpful for operators to diagnose configuration problems quickly.

## Supporting actions

- **Version bump script**: automate updating `SWITCHYARD_VERSION` and related image tags.
- **Validation error class**: unify error handling across the UI.
- **Logging improvements**: emit stack traces for unexpected exceptions.
- **Contributing guide**: create `CONTRIBUTING.md` with steps to run the web UI and extend route types.

---

*These goals are tracked in‑repo to ensure visibility for contributors and maintainers.*