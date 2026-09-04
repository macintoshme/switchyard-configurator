# Project Goals – Switchyard Configurator

## Objectives

1. **Refactor route-type handling**
   - Introduce a registry/plugin architecture (`route_type -> handler`) that encapsulates TOML conversion, validation, and UI rendering.
   - New route types can be added without touching multiple modules.

2. **Document and improve security**
   - Add a dedicated **SECURITY.md** explaining the implications of mounting the Docker socket and storing secrets in `.env`.
   - Offer alternative deployment guides (Docker secrets, Kubernetes Secrets, TLS-secured Docker API).

3. **Optimize Rust build pipeline**
   - Use `cargo-chef` to cache dependencies and speed up rebuilds.
   - Optionally provide a `scratch`-based final image for minimal footprint.

4. **Expose debugging endpoint**
   - Add a simple HTTP endpoint (e.g., `/debug/cycles`) that returns any detected route cycles, so an operator can see which routes form a loop before a request hits it.

## Supporting actions

- **Version bump script**: automate updating `SWITCHYARD_VERSION` and related image tags.
- **Validation error class**: unify error handling across the UI.
- **Logging improvements**: emit stack traces for unexpected exceptions.
- **Contributing guide**: create `CONTRIBUTING.md` with steps to run the web UI and extend route types.

