#!/bin/bash

REPO_DIR=~/homeLab
CADDY_DIR=/etc/caddy
SYSTEMD_OVERRIDE=/etc/systemd/system/caddy.service.d/override.conf

DEPLOY=false
if [[ "$1" == "deploy" ]]; then
    DEPLOY=true
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
