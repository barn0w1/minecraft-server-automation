#!/usr/bin/env bash
set -euo pipefail

# mcctl.sh - local helper to control the Minecraft world via Lambda Function URL
# and to connect to ephemeral EC2 via SSM Session Manager.
#
# Usage:
#   ./mcctl.sh start  <world>
#   ./mcctl.sh stop   <world>
#   ./mcctl.sh snapshot <world>
#   ./mcctl.sh status <world>
#   ./mcctl.sh connect <world>
#   ./mcctl.sh outputs

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$ROOT_DIR/terraform"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing command: $1" >&2
    exit 1
  }
}

need_cmd terraform
need_cmd aws
need_cmd curl

# terraform output helper
_tf_out() {
  local name="$1"
  terraform -chdir="$TF_DIR" output -raw "$name" 2>/dev/null
}

function_url() {
  local url
  url="$(_tf_out function_url)"
  if [[ -z "$url" ]]; then
    echo "Failed to read terraform output: function_url" >&2
    exit 1
  fi
  echo "$url"
}

ddb_table() {
  local t
  t="$(_tf_out dynamodb_table_name)"
  if [[ -z "$t" ]]; then
    echo "Failed to read terraform output: dynamodb_table_name" >&2
    exit 1
  fi
  echo "$t"
}

world_required() {
  local world="${1:-}"
  if [[ -z "$world" ]]; then
    echo "World name required." >&2
    exit 1
  fi
}

cmd_outputs() {
  echo "function_url=$(_tf_out function_url)"
  echo "config_bucket_name=$(_tf_out config_bucket_name || true)"
  echo "snapshot_bucket_name=$(_tf_out snapshot_bucket_name || true)"
  echo "dynamodb_table_name=$(_tf_out dynamodb_table_name)"
}

cmd_start() {
  local world="$1"
  local url
  url="$(function_url)"
  curl -sS -X POST "$url" \
    -H "Content-Type: application/json" \
    -d "{\"action\":\"start\",\"world\":\"$world\"}" | cat
}

cmd_stop() {
  local world="$1"
  local url
  url="$(function_url)"
  curl -sS -X POST "$url" \
    -H "Content-Type: application/json" \
    -d "{\"action\":\"stop\",\"world\":\"$world\"}" | cat
}

cmd_status() {
  local world="$1"
  local url
  url="$(function_url)"
  curl -sS "${url}?action=status&world=${world}" | cat
}

cmd_snapshot() {
  local world="$1"
  local url
  url="$(function_url)"
  curl -sS -X POST "$url" \
    -H "Content-Type: application/json" \
    -d "{\"action\":\"snapshot\",\"world\":\"$world\"}" | cat
}

cmd_connect() {
  local world="$1"
  local table
  table="$(ddb_table)"

  # Query DynamoDB for instance_id
  local instance_id
  instance_id=$(aws dynamodb get-item \
    --table-name "$table" \
    --key "{\"world\":{\"S\":\"$world\"}}" \
    --query "Item.instance_id.S" \
    --output text 2>/dev/null || true)

  if [[ -z "$instance_id" || "$instance_id" == "None" ]]; then
    echo "No instance_id found for world '$world' in DynamoDB table '$table'." >&2
    echo "Try: ./mcctl.sh status $world" >&2
    exit 1
  fi

  echo "Starting SSM session to instance: $instance_id" >&2
  echo "(If this fails, ensure your IAM user has ssm:StartSession and Session Manager plugin is installed.)" >&2

  aws ssm start-session --target "$instance_id"
}

main() {
  local cmd="${1:-}"
  shift || true

  case "$cmd" in
    outputs)
      cmd_outputs
      ;;
    start)
      world_required "${1:-}"; cmd_start "$1"
      ;;
    stop)
      world_required "${1:-}"; cmd_stop "$1"
      ;;
    status)
      world_required "${1:-}"; cmd_status "$1"
      ;;
    snapshot)
      world_required "${1:-}"; cmd_snapshot "$1"
      ;;
    connect)
      world_required "${1:-}"; cmd_connect "$1"
      ;;
    ""|help|-h|--help)
      sed -n '1,40p' "$0" | sed 's/^# \{0,1\}//'
      ;;
    *)
      echo "Unknown command: $cmd" >&2
      echo "Run: ./mcctl.sh help" >&2
      exit 1
      ;;
  esac
}

main "$@"
