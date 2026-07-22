#!/usr/bin/env bash
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Emit the ~/.claude/settings.json needed to send Claude Code telemetry to this
# collector.
#
# Usage:
#   ./print-settings.sh [email] [--endpoint URL] [--merge]
#
#   (no flag)     Print the complete, correctly-shaped settings.json to stdout.
#                 IMPORTANT: `otelHeadersHelper` is a TOP-LEVEL key, a sibling of
#                 "env" — NOT one of the env vars inside it.
#   --endpoint    Collector URL, if you have no .collector-url (ask your admin).
#                 Otherwise it is read from .collector-url.
#   --merge       Merge the keys into ~/.claude/settings.json in place (backs the
#                 file up first). Foolproof — puts each key where it goes.
#                 Uses jq if present, otherwise falls back to Python.
#
# On Windows the emitted `otelHeadersHelper` points at otel-headers-helper.cmd,
# not the .sh: Claude Code always runs this value through cmd.exe on Windows,
# and cmd cannot execute a .sh — it returns success having produced no output,
# so telemetry ships with an empty Authorization header and is rejected.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
URL_FILE="${SCRIPT_DIR}/.collector-url"
SETTINGS="${HOME}/.claude/settings.json"

# shellcheck source=lib-common.sh
source "${SCRIPT_DIR}/lib-common.sh"

# Parse args: an email (anything not starting with --) and/or flags.
EMAIL="you@example.com"
MERGE=0
ENDPOINT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --merge)    MERGE=1; shift ;;
    --endpoint) ENDPOINT="${2:-}"; [[ -n "${ENDPOINT}" ]] || { echo "print-settings: --endpoint requires a URL" >&2; exit 2; }; shift 2 ;;
    --*)        echo "print-settings: unknown flag $1" >&2; exit 1 ;;
    *)          EMAIL="$1"; shift ;;
  esac
done

if [[ -z "${ENDPOINT}" ]]; then
  if [[ ! -s "${URL_FILE}" ]]; then
    echo "print-settings: ${URL_FILE} not found." >&2
    echo "  If you are the admin:  ./deploy.sh" >&2
    echo "  Otherwise ask your admin for the collector URL, then either:" >&2
    echo "    ./setup.sh --developer --project <id> --endpoint <url> -y" >&2
    echo "    ./print-settings.sh ${EMAIL} --endpoint <url>" >&2
    exit 1
  fi
  ENDPOINT="$(cat "${URL_FILE}")"
fi
# A trailing slash changes the ID-token audience and silently breaks auth.
ENDPOINT="${ENDPOINT%/}"

# ---- Resolve the helper path for this platform ------------------------------
case "$(uname -s 2>/dev/null || echo unknown)" in
  MINGW*|MSYS*|CYGWIN*)
    # cmd.exe needs a native path, and the docs require the value to be quoted
    # inside the JSON when the path contains spaces — so always quote it.
    HELPER_WIN="$(cygpath -w "${SCRIPT_DIR}/otel-headers-helper.cmd")"
    HELPER="\"${HELPER_WIN}\""
    ;;
  *)
    HELPER="${SCRIPT_DIR}/otel-headers-helper.sh"
    ;;
esac

# JSON-escape backslashes and quotes (only needed for the printed form; jq and
# Python escape their own arguments).
json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '%s' "${s}"
}

# ---- --merge: edit ~/.claude/settings.json in place -------------------------
if [[ "${MERGE}" -eq 1 ]]; then
  mkdir -p "$(dirname "${SETTINGS}")"
  [[ -s "${SETTINGS}" ]] || echo '{}' > "${SETTINGS}"
  BACKUP="${SETTINGS}.bak.$(date +%Y%m%d-%H%M%S)"
  cp "${SETTINGS}" "${BACKUP}"
  TMP="$(mktemp)"

  if command -v jq >/dev/null 2>&1; then
    jq \
      --arg ep "${ENDPOINT}" --arg email "user.email=${EMAIL}" --arg helper "${HELPER}" '
      .env = ((.env // {}) + {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
        "OTEL_EXPORTER_OTLP_ENDPOINT": $ep,
        "OTEL_METRIC_EXPORT_INTERVAL": "10000",
        "OTEL_LOGS_EXPORT_INTERVAL": "5000",
        "OTEL_RESOURCE_ATTRIBUTES": $email
      })
      | .otelHeadersHelper = $helper
    ' "${SETTINGS}" > "${TMP}"
  else
    # jq is not installed on plenty of machines (notably a stock Windows box),
    # and this used to be a hard failure. Python is a fine stand-in for a merge
    # this small; the Cloud SDK's bundled interpreter counts, since anyone using
    # this collector already has gcloud.
    PY="$(find_python || true)"
    if [[ -z "${PY}" ]]; then
      echo "print-settings: --merge needs jq or Python, and found neither." >&2
      echo "  Run without --merge and paste the printed JSON yourself." >&2
      rm -f "${TMP}"
      exit 1
    fi
    "${PY}" - "${SETTINGS}" "${ENDPOINT}" "user.email=${EMAIL}" "${HELPER}" > "${TMP}" <<'PYEOF'
import json, sys
path, endpoint, email_attr, helper = sys.argv[1:5]
with open(path) as fh:
    try:
        data = json.load(fh)
    except ValueError:
        sys.exit("print-settings: %s is not valid JSON; fix or move it first." % path)
if not isinstance(data, dict):
    sys.exit("print-settings: %s must contain a JSON object." % path)
env = data.get("env") or {}
env.update({
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
    "OTEL_METRIC_EXPORT_INTERVAL": "10000",
    "OTEL_LOGS_EXPORT_INTERVAL": "5000",
    "OTEL_RESOURCE_ATTRIBUTES": email_attr,
})
data["env"] = env
data["otelHeadersHelper"] = helper
json.dump(data, sys.stdout, indent=2)
sys.stdout.write("\n")
PYEOF
  fi

  # Never leave a truncated settings.json behind if the merge produced nothing.
  if [[ ! -s "${TMP}" ]]; then
    echo "print-settings: merge produced no output; ${SETTINGS} left unchanged." >&2
    rm -f "${TMP}"
    exit 1
  fi
  mv "${TMP}" "${SETTINGS}"
  echo "==> Updated ${SETTINGS}"
  echo "    Backup: ${BACKUP}"
  echo "    (otelHeadersHelper set at top level; OTEL_* keys merged into .env)"
  echo "    Restart Claude Code to apply."
  exit 0
fi

# ---- default: print the complete, correctly-shaped settings.json ------------
cat <<EOF
# Complete ~/.claude/settings.json shape. Note the structure:
#   - the OTEL_* keys go INSIDE "env"
#   - "otelHeadersHelper" is a TOP-LEVEL key, a SIBLING of "env" (not inside it)
# If you already have a settings.json, merge these in (or re-run with --merge to
# do it automatically). Then restart Claude Code.

{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "${ENDPOINT}",
    "OTEL_METRIC_EXPORT_INTERVAL": "10000",
    "OTEL_LOGS_EXPORT_INTERVAL": "5000",
    "OTEL_RESOURCE_ATTRIBUTES": "user.email=${EMAIL}"
  },
  "otelHeadersHelper": "$(json_escape "${HELPER}")"
}
EOF
