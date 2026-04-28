#!/bin/bash

REPO_DIR=~/homeLab
CADDY_DIR=/etc/caddy
SYSTEMD_OVERRIDE=/etc/systemd/system/caddy.service.d/override.conf
CADDY_ENV="$CADDY_DIR/caddy.env"

MODE="$1"  # deploy | deploy-reload | deploy-restart

if [[ "$MODE" != "deploy" && "$MODE" != "deploy-reload" && "$MODE" != "deploy-restart" ]]; then
    echo "Usage: $0 <deploy|deploy-reload|deploy-restart>"
    echo ""
    echo "  deploy          Copy changed files. Warn if a restart is required."
    echo "  deploy-reload   Copy changed files and reload Caddy. Warn if a restart is required."
    echo "  deploy-restart  Copy changed files and restart Caddy (daemon-reload + restart)."
    exit 1
fi

DEPLOY=true

# Load secrets from live caddy.env so variables like PATH_WWW are available
if [[ -f "$CADDY_ENV" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$CADDY_ENV"
    set +a
else
    echo "WARNING: $CADDY_ENV not found — PATH_WWW and other secrets will not be set."
fi

# Pull latest from GitHub
echo "==> Pulling latest from GitHub..."
cd "$REPO_DIR" && git pull

echo ""

# Define file mappings: source (repo) -> destination (live)
declare -A FILES
FILES["$REPO_DIR/caddy/Caddyfile"]="$CADDY_DIR/Caddyfile"
FILES["$REPO_DIR/caddy/ca.crt"]="$CADDY_DIR/ca.crt"
FILES["$REPO_DIR/caddy/override.conf"]="$SYSTEMD_OVERRIDE"

CHANGES=false
RESTART_REQUIRED=false

for SRC in "${!FILES[@]}"; do
    DEST="${FILES[$SRC]}"

    if [[ ! -f "$SRC" ]]; then
        echo "WARNING: $SRC not found in repo, skipping."
        continue
    fi

    if [[ ! -f "$DEST" ]]; then
        echo "NEW: $DEST does not exist on server."
        CHANGES=true
        [[ "$DEST" == "$SYSTEMD_OVERRIDE" ]] && RESTART_REQUIRED=true
        echo "==> Copying $SRC -> $DEST"
        cp "$SRC" "$DEST"
        continue
    fi

    if ! diff -q "$SRC" "$DEST" > /dev/null 2>&1; then
        CHANGES=true
        [[ "$DEST" == "$SYSTEMD_OVERRIDE" ]] && RESTART_REQUIRED=true
        echo "CHANGED: $DEST"
        diff "$SRC" "$DEST"
        echo ""

        echo "==> Copying $SRC -> $DEST"
        cp "$SRC" "$DEST"

        # Set permissions based on destination
        case "$DEST" in
            "$CADDY_DIR/Caddyfile")
                chown root:caddy "$DEST"
                chmod 640 "$DEST"
                ;;
            "$CADDY_DIR/ca.crt")
                chown root:caddy "$DEST"
                chmod 640 "$DEST"
                ;;
            "$SYSTEMD_OVERRIDE")
                chown root:root "$DEST"
                chmod 644 "$DEST"
                ;;
        esac
    else
        echo "OK: $DEST"
    fi
done

echo ""

# Copy HTML files from repo to PATH_WWW
if [[ -z "$PATH_WWW" ]]; then
    echo "WARNING: PATH_WWW is not set — skipping HTML file deployment."
else
    echo "==> Checking HTML files -> $PATH_WWW"
    for SRC in "$REPO_DIR"/caddy/*.html; do
        [[ -f "$SRC" ]] || continue
        DEST="$PATH_WWW/$(basename "$SRC")"

        if [[ ! -f "$DEST" ]]; then
            echo "NEW: $DEST does not exist."
            CHANGES=true
            echo "==> Copying $SRC -> $DEST"
            cp "$SRC" "$DEST"
        elif ! diff -q "$SRC" "$DEST" > /dev/null 2>&1; then
            CHANGES=true
            echo "CHANGED: $DEST"
            diff "$SRC" "$DEST"
            echo ""
            echo "==> Copying $SRC -> $DEST"
            cp "$SRC" "$DEST"
        else
            echo "OK: $DEST"
        fi
    done
fi

echo ""

if ! $CHANGES; then
    echo "==> No changes, nothing to deploy."
    exit 0
fi

if $RESTART_REQUIRED; then
    echo "WARNING: override.conf changed — a full restart is required to apply systemd service changes."
fi

case "$MODE" in
    deploy)
        echo "==> Files deployed. Run with 'deploy-reload' or 'deploy-restart' to apply changes."
        ;;
    deploy-reload)
        if $RESTART_REQUIRED; then
            echo "WARNING: Reload will NOT apply override.conf changes. Run 'deploy-restart' to fully apply."
        fi
        echo "==> Reloading Caddy..."
        caddy reload --config "$CADDY_DIR/Caddyfile"
        ;;
    deploy-restart)
        echo "==> Restarting Caddy..."
        systemctl daemon-reload
        systemctl restart caddy
        ;;
esac
