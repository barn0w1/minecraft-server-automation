#!/usr/bin/env bash
set -euo pipefail

WORLD="${1:-test}"
"$(cd "$(dirname "$0")" && pwd)/mcctl.sh" status "$WORLD"