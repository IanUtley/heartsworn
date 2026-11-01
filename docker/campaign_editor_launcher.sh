#!/bin/sh
# Start the optional campaign editor (campaign_editor/ at the project root)
# when the campaign it edits (campaign/ at the project root, or
# HEX_CAMPAIGN_ROOT) is present.
set -eu

BASE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
EDITOR_SCRIPT="$BASE_DIR/campaign_editor/server.py"

CAMPAIGN_DATA=${HEX_CAMPAIGN_ROOT:-$BASE_DIR/campaign}

if [ ! -f "$EDITOR_SCRIPT" ] || [ ! -d "$CAMPAIGN_DATA/campaign_runtime" ]; then
    echo "[campaign_editor] editor or campaign source not found; skipping"
    exit 0
fi

EDITOR_HOST=${HEX_CAMPAIGN_EDITOR_HOST:-127.0.0.1}
EDITOR_PORT=${HEX_CAMPAIGN_EDITOR_PORT:-8765}
if [ -n "${HEX_DB_PATH:-}" ]; then
    EDITOR_DB=$HEX_DB_PATH
else
    EDITOR_DB="$BASE_DIR/hconnect.db"
fi

if [ "${HEX_CAMPAIGN_EDITOR_TLS:-0}" = "1" ]; then
    # Log-in passwords go over this listener, so serve HTTPS. Use the
    # certificate the operator supplies, else a self-signed one kept in the
    # state directory (reused across restarts so browsers keep trusting it).
    if [ -n "${HEX_CAMPAIGN_EDITOR_CERT:-}" ] || [ -n "${HEX_CAMPAIGN_EDITOR_KEY:-}" ]; then
        EDITOR_CERT=${HEX_CAMPAIGN_EDITOR_CERT:?set HEX_CAMPAIGN_EDITOR_KEY with the certificate}
        EDITOR_KEY=${HEX_CAMPAIGN_EDITOR_KEY:?set HEX_CAMPAIGN_EDITOR_CERT with the key}
    else
        if [ -n "${HEX_DB_PATH:-}" ]; then
            TLS_DIR=$(dirname "$HEX_DB_PATH")/tls
        else
            TLS_DIR="$BASE_DIR/generated/tls"
        fi
        EDITOR_CERT="$TLS_DIR/campaign_editor.crt"
        EDITOR_KEY="$TLS_DIR/campaign_editor.key"
        if [ ! -s "$EDITOR_CERT" ] || [ ! -s "$EDITOR_KEY" ]; then
            mkdir -p "$TLS_DIR"
            # Subject alternative names: the hosts a browser will type in.
            # Default covers this machine; add the public IP or DNS name with
            # HEX_CAMPAIGN_EDITOR_TLS_NAMES (comma-separated).
            SAN=""
            OLD_IFS=$IFS
            IFS=,
            for name in ${HEX_CAMPAIGN_EDITOR_TLS_NAMES:-localhost,127.0.0.1}; do
                name=$(printf '%s' "$name" | tr -d ' ')
                [ -n "$name" ] || continue
                case "$name" in
                    *[!0-9.]*) SAN="${SAN:+$SAN,}DNS:$name" ;;
                    *) SAN="${SAN:+$SAN,}IP:$name" ;;
                esac
            done
            IFS=$OLD_IFS
            echo "[campaign_editor] generating self-signed certificate in $TLS_DIR ($SAN)"
            umask 077
            openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
                -days 3650 -subj "/CN=hex-campaign-editor" -addext "subjectAltName=$SAN" \
                -keyout "$EDITOR_KEY.tmp" -out "$EDITOR_CERT.tmp" 2>/dev/null
            mv "$EDITOR_KEY.tmp" "$EDITOR_KEY"
            mv "$EDITOR_CERT.tmp" "$EDITOR_CERT"
        fi
    fi
    set -- "$@" --cert "$EDITOR_CERT" --key "$EDITOR_KEY"
fi

exec python3 -u "$EDITOR_SCRIPT" \
    --host "$EDITOR_HOST" --port "$EDITOR_PORT" --db "$EDITOR_DB" "$@"
