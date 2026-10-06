#!/usr/bin/env bash
set -euo pipefail

MARKER="${BOOTSTRAP_MARKER:-/app/.plugins_bootstrapped}"
PLUGINS_DIR="${PLUGINS_DIR:-/home/alerta/.local}"
BOOT_PLUGINS="${BOOT_PLUGINS:-}"
PLUGINS_FILE="${PLUGINS_FILE:-}"

bootstrap_plugins() {
    local pkgs=()

    if [[ -n "${PLUGINS_FILE}" && -f "${PLUGINS_FILE}" ]]; then
        echo "[bootstrap] reading plugins from ${PLUGINS_FILE}"
        while IFS= read -r line || [[ -n "${line}" ]]; do
            line="${line%%#*}"                     # strip comments
            line="$(echo -e "${line}" | xargs)"    # trim
            [[ -n "${line}" ]] && pkgs+=("${line}")
        done < "${PLUGINS_FILE}"
    fi

    if [[ -n "${BOOT_PLUGINS}" ]]; then
        # supports space- and comma-separated values
        local normalized="${BOOT_PLUGINS//,/ }"
        # shellcheck disable=SC2206
        pkgs+=(${normalized})
    fi

    if [[ ${#pkgs[@]} -eq 0 ]]; then
        echo "[bootstrap] no plugins configured, skipping"
        return 0
    fi

    echo "[bootstrap] installing plugins: ${pkgs[*]}"
    mkdir -p "${PLUGINS_DIR}"
    pip install --user --no-cache-dir "${pkgs[@]}"
    echo "[bootstrap] plugins installed"
}

if [[ ! -f "${MARKER}" ]]; then
    echo "[bootstrap] first run detected (${MARKER} not found)"
    bootstrap_plugins
    touch "${MARKER}" || true
else
    echo "[bootstrap] marker ${MARKER} found, skipping plugin install"
fi

exec "$@"
