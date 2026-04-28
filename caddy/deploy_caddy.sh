#!/bin/bash

REPO_DIR=~/homeLab
CADDY_DIR=/etc/caddy
SYSTEMD_OVERRIDE=/etc/systemd/system/caddy.service.d/override.conf
CADDY_ENV="$CADDY_DIR/caddy.env"

DEPLOY=false
if [[ "$1" == "deploy" ]]; then
    DEPLOY=true
fi

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

for SRC in "${!FILES[@]}"; do
    DEST="${FILES[$SRC]}"

    if [[ ! -f "$SRC" ]]; then
        echo "WARNING: $SRC not found in repo, skipping."
        continue
    fi

    if [[ ! -f "$DEST" ]]; then
        echo "NEW: $DEST does not exist on server."
        CHANGES=true
        if $DEPLOY; then
            echo "==> Copying $SRC -> $DEST"
            cp "$SRC" "$DEST"
        fi
        continue
    fi

    if ! diff -q "$SRC" "$DEST" > /dev/null 2>&1; then
        CHANGES=true
        echo "CHANGED: $DEST"
        diff "$SRC" "$DEST"
        echo ""

        if $DEPLOY; then
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
        fi
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
            if $DEPLOY; then
                echo "==> Copying $SRC -> $DEST"
                cp "$SRC" "$DEST"
            fi
        elif ! diff -q "$SRC" "$DEST" > /dev/null 2>&1; then
            CHANGES=true
            echo "CHANGED: $DEST"
            diff "$SRC" "$DEST"
            echo ""
            if $DEPLOY; then
                echo "==> Copying $SRC -> $DEST"
                cp "$SRC" "$DEST"
            fi
        else
            echo "OK: $DEST"
        fi
    done
fi

echo ""

if $DEPLOY && $CHANGES; then
    if diff -q "$REPO_DIR/caddy/override.conf" "$SYSTEMD_OVERRIDE" > /dev/null 2>&1; then
        echo "==> Reloading Caddy..."
        # caddy reload --config "$CADDY_DIR/Caddyfile"
    else
        echo "==> override.conf changed, restarting Caddy..."
        # systemctl daemon-reload
        # systemctl restart caddy
    fi
elif $DEPLOY && ! $CHANGES; then
    echo "==> No changes, nothing to deploy."
fi
