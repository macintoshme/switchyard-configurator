#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# Runs the configurator as the host user (so bind-mount writes keep normal
# file ownership) while granting the docker socket's group id, so the
# "restart switchyard" action can talk to the daemon.
#
# Detection is needed because the docker socket's group id varies across
# setups: root:docker(660) on plain Linux, and it is remapped on Rancher
# Desktop / OrbStack. Reading it here (after the mount is in place) avoids
# hard-coding a gid in the compose file.

set -e

APP_UID="${CONFIGURATOR_UID:-1000}"
APP_GID="${CONFIGURATOR_GID:-1000}"

SOCK_GID=""
if [ -S /var/run/docker.sock ] && command -v stat >/dev/null 2>&1; then
    SOCK_GID="$(stat -c %g /var/run/docker.sock 2>/dev/null || true)"
fi

if command -v setpriv >/dev/null 2>&1; then
    if [ -n "$SOCK_GID" ] && [ "$SOCK_GID" != "0" ]; then
        # --clear-groups and --groups are mutually exclusive:
        # --groups already *replaces* the supplementary group list.
        exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --groups="$SOCK_GID" "$@"
    fi
    exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --clear-groups "$@"
fi

exec "$@"
