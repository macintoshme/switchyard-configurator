# SPDX-License-Identifier: Apache-2.0
#
# Multi-stage build of NVIDIA NeMo Switchyard's standalone Rust server.
# No prebuilt public image is published, so we build the release binary
# from the pinned upstream tag. Mirrors the upstream Dockerfile but adds
# a git clone step so this stack is self-contained.
#
# Build args:
#   RUST_VERSION        - rust toolchain image tag (must satisfy rust-toolchain.toml)
#   SWITCHYARD_VERSION  - upstream git tag / branch to build

ARG RUST_VERSION=1.96.1
ARG SWITCHYARD_VERSION=v0.2.0

FROM rust:${RUST_VERSION}-bookworm AS builder
ARG SWITCHYARD_VERSION

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/switchyard

# Clone the pinned release. --depth 1 keeps the layer small.
RUN git clone --depth 1 --branch "${SWITCHYARD_VERSION}" \
        https://github.com/NVIDIA-NeMo/Switchyard.git .

# Build the server binary. --locked uses the committed Cargo.lock for reproducibility.
RUN cargo build --locked --release -p switchyard-server

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder \
    /opt/switchyard/target/release/switchyard-server \
    /usr/local/bin/switchyard-server

ENV HOME=/tmp \
    RUST_LOG=switchyard_server=info,libsy=info

USER 1000:1000
EXPOSE 4000

# The port is configurable at runtime via SWITCHYARD_PORT (default 4000).
# `exec` keeps switchyard-server as PID 1 so it receives signals directly.
CMD ["sh", "-c", "exec switchyard-server --config /etc/switchyard/routes.toml --host 0.0.0.0 --port ${SWITCHYARD_PORT:-4000}"]