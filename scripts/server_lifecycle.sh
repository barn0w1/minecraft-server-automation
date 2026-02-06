#!/bin/bash
# server_lifecycle.sh
#
# Storage layout:
#   - Data bucket (latest):     s3://<data_bucket>/worlds/<world>/{compose.yaml,data.tar}
#   - Snapshot bucket (history): s3://<snapshot_bucket>/worlds/<world>/snapshots/<ts>.tar
#
# Usage:
#   ./server_lifecycle.sh start    <world> <data_bucket> <snapshot_bucket>
#   ./server_lifecycle.sh stop     <world> <data_bucket> <snapshot_bucket>
#   ./server_lifecycle.sh snapshot <world> <data_bucket> <snapshot_bucket>
#   ./server_lifecycle.sh status   <world> <data_bucket> <snapshot_bucket>

set -euo pipefail

ACTION="${1:-}"
WORLD="${2:-}"
DATA_BUCKET="${3:-}"
SNAPSHOT_BUCKET="${4:-}"
BASE_DIR="/opt/minecraft"

# Load config if not provided (for stop/status called via SSM without args)
if [ -f "$BASE_DIR/.config" ]; then
    # shellcheck disable=SC1090
    source "$BASE_DIR/.config"
fi

# Backward compatibility with older .config
if [ -z "$DATA_BUCKET" ] && [ -n "${CONFIG_BUCKET:-}" ]; then
    DATA_BUCKET="$CONFIG_BUCKET"
fi
if [ -z "$DATA_BUCKET" ] && [ -n "${BUCKET:-}" ]; then
    DATA_BUCKET="$BUCKET"
fi
if [ -z "$SNAPSHOT_BUCKET" ] && [ -n "$DATA_BUCKET" ]; then
    SNAPSHOT_BUCKET="$DATA_BUCKET"
fi

if [ -z "$ACTION" ] || [ -z "$WORLD" ] || [ -z "$DATA_BUCKET" ] || [ -z "$SNAPSHOT_BUCKET" ]; then
    echo "Usage: $0 [start|stop|snapshot|status] <world_name> <data_bucket> <snapshot_bucket>"
    exit 1
fi

mkdir -p "$BASE_DIR"
cd $BASE_DIR

function log() {
    echo "[$(date)] $1"
}

function has_compose_file() {
    [ -f "$BASE_DIR/compose.yaml" ]
}

function has_docker_compose() {
    command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1
}

function snapshot_ts() {
    date -u +"%Y-%m-%dT%H-%M-%SZ"
}

function data_prefix() {
    echo "worlds/$WORLD"
}

function write_config() {
    {
        echo "WORLD=$WORLD"
        echo "DATA_BUCKET=$DATA_BUCKET"
        echo "SNAPSHOT_BUCKET=$SNAPSHOT_BUCKET"
    } > "$BASE_DIR/.config"
}

function restore_data_from_latest_tar() {
    local key
    key="$(data_prefix)/data.tar"
    log "Restoring latest data.tar from s3://$DATA_BUCKET/$key (if exists)..."
    if aws s3 cp "s3://$DATA_BUCKET/$key" - 2>/dev/null | tar -xf - -C "$BASE_DIR"; then
        log "Restored data from $key"
    else
        log "No data.tar found. Starting with empty data/."
        mkdir -p "$BASE_DIR/data"
    fi
}

function ensure_compose_yaml() {
    local key
    key="$(data_prefix)/compose.yaml"
    log "Fetching compose.yaml from s3://$DATA_BUCKET/$key ..."
    if aws s3 cp "s3://$DATA_BUCKET/$key" "$BASE_DIR/compose.yaml"; then
        return 0
    fi

    log "compose.yaml not found. Creating default locally (not uploading)."
    cat <<EOF > "$BASE_DIR/compose.yaml"
services:
  mc:
    image: itzg/minecraft-server
    ports:
      - "25565:25565"
    environment:
      EULA: "TRUE"
      VERSION: "LATEST"
      TYPE: "PAPER"
    volumes:
      - ./data:/data
    restart: always
EOF
}

function maybe_flush_world() {
    if ! has_compose_file || ! has_docker_compose; then
        log "compose.yaml or docker compose missing; skipping save-all"
        return 0
    fi

    if ! docker compose ps 2>/dev/null | grep -q "Up"; then
        return 0
    fi

    local service
    service="$(detect_minecraft_service || true)"
    if [ -z "$service" ]; then
        log "Minecraft service not detected; skipping save-all"
        return 0
    fi

    log "Flushing world via rcon-cli (save-all)"
    docker compose exec -T "$service" rcon-cli save-all >/dev/null 2>&1 || true
}

function upload_snapshot_and_latest() {
    local ts snapshot_key data_key
    ts="$(snapshot_ts)"
    snapshot_key="$(data_prefix)/snapshots/$ts.tar"
    data_key="$(data_prefix)/data.tar"

    mkdir -p "$BASE_DIR/data"

    log "Uploading snapshot tar to s3://$SNAPSHOT_BUCKET/$snapshot_key"
    tar -cf - -C "$BASE_DIR" data | aws s3 cp - "s3://$SNAPSHOT_BUCKET/$snapshot_key"

    log "Updating latest data.tar to s3://$DATA_BUCKET/$data_key"
    tar -cf - -C "$BASE_DIR" data | aws s3 cp - "s3://$DATA_BUCKET/$data_key"

    log "Snapshot created: $snapshot_key"
}

function detect_minecraft_service() {
    # 1) Allow explicit override via .config
    if [ -f "$BASE_DIR/.config" ]; then
        MC_SERVICE_FROM_CONFIG=$(grep -E '^MC_SERVICE=' "$BASE_DIR/.config" | head -n 1 | cut -d= -f2-)
        if [ -n "$MC_SERVICE_FROM_CONFIG" ]; then
            echo "$MC_SERVICE_FROM_CONFIG"
            return 0
        fi
    fi

    # 2) Prefer the service whose image includes itzg/minecraft-server
    # docker compose config output looks like:
    # services:
    #   svcname:
    #     image: itzg/minecraft-server
    MC_SERVICE=$(docker compose config 2>/dev/null | awk '
        /^services:/ {in_services=1; next}
        in_services && /^[[:space:]]{2}[A-Za-z0-9_.-]+:$/ {
            current=$1; sub(/:$/, "", current); next
        }
        in_services && /image:[[:space:]]*/ && /itzg\/minecraft-server/ {
            print current; exit
        }
    ')
    if [ -n "$MC_SERVICE" ]; then
        echo "$MC_SERVICE"
        return 0
    fi

    # 3) Fallback: if exactly one service exists, use it
    SERVICES=$(docker compose config --services 2>/dev/null | sed '/^$/d')
    if [ -n "$SERVICES" ]; then
        COUNT=$(echo "$SERVICES" | wc -l | tr -d ' ')
        if [ "$COUNT" = "1" ]; then
            echo "$SERVICES"
            return 0
        fi
    fi

    # 4) Last resort: first running service
    RUNNING=$(docker compose ps --services 2>/dev/null | sed '/^$/d' | head -n 1)
    if [ -n "$RUNNING" ]; then
        echo "$RUNNING"
        return 0
    fi

    return 1
}

case "$ACTION" in
    start)
    log "Starting world: $WORLD"
    log "Data bucket: $DATA_BUCKET"
    log "Snapshot bucket: $SNAPSHOT_BUCKET"

    write_config
    ensure_compose_yaml
    restore_data_from_latest_tar

    log "Starting Docker Compose..."
    docker compose up -d
        ;;

    stop)
        log "Stopping world: $WORLD"

        maybe_flush_world

        if has_compose_file && has_docker_compose; then
            docker compose down || true
        else
            log "compose.yaml or docker compose missing; skipping docker compose down"
        fi
        upload_snapshot_and_latest

        # Terminate Self
        log "Terminating instance..."
        TOKEN=$(curl -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
        INSTANCE_ID=$(curl -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id)
        aws ec2 terminate-instances --instance-ids $INSTANCE_ID --region $(curl -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/region)
        ;;

    snapshot)
        log "Snapshotting world (manual): $WORLD"
        log "Data bucket: $DATA_BUCKET"
        log "Snapshot bucket: $SNAPSHOT_BUCKET"

        write_config
        maybe_flush_world
        upload_snapshot_and_latest
        ;;

    status)
        # Check player count using rcon-cli inside container
        if ! has_compose_file || ! has_docker_compose; then
            echo "STATUS: UNKNOWN"
            echo "PLAYERS: UNKNOWN (compose.yaml or docker compose missing)"
            exit 0
        fi

        if docker compose ps | grep -q "Up"; then
            SERVICE=$(detect_minecraft_service)
            if [ -z "$SERVICE" ]; then
                echo "STATUS: RUNNING"
                echo "PLAYERS: UNKNOWN (minecraft service not detected)"
                exit 0
            fi

            PLAYERS=$(docker compose exec -T "$SERVICE" rcon-cli list 2>/dev/null || true)
            echo "STATUS: RUNNING"
            echo "PLAYERS: $PLAYERS"
        else
            echo "STATUS: STOPPED"
        fi
        ;;

    *)
        echo "Unknown action: $ACTION" >&2
        exit 1
        ;;
esac
