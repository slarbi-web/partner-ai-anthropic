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
# Setup: writes config.env (and, when an endpoint is supplied, .collector-url)
# and renders the SQL templates to project-specific *.local.sql files.
# Safe to re-run — existing config.env values become the defaults.
#
# Two modes:
#   (default)     admin — everything needed to deploy the collector.
#   --developer   developer — point this machine at an ALREADY-DEPLOYED
#                 collector. Needs only --project and --endpoint; the
#                 admin-only questions are skipped. Requires no deploy rights.
#
# Interactive by default; every value can also be supplied as a flag, and
# --non-interactive turns off prompting entirely (for CI / scripted installs).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXAMPLE="${SCRIPT_DIR}/config.env.example"
CONFIG="${SCRIPT_DIR}/config.env"
URL_FILE="${SCRIPT_DIR}/.collector-url"

usage() {
  cat <<'EOF'
Usage: ./setup.sh [options]

Modes:
  --developer               Configure this machine to SEND telemetry to an
                            already-deployed collector. Only --project and
                            --endpoint are needed; admin questions are skipped.
                            (Default mode is admin: full deploy configuration.)

Values (any of these may also be answered interactively):
  --project ID              GCP project ID
  --endpoint URL            Collector service URL; written to .collector-url.
                            Ask your admin for this if you did not deploy.
  --region REGION           Cloud Run region
  --service NAME            Cloud Run service name
  --runtime-sa NAME         Collector runtime SA name
  --invoker-sa NAME         Shared invoker SA name (laptop devs impersonate it)
  --developers "MEMBERS"    Admin only. Who may send telemetry; space-separated
                            IAM members, e.g. "domain:yourco.com"
  --invoker-members "M..."  Admin only. Direct-invoker machine SAs
  --min-instances N         Admin only. 1 = always-on capture, 0 = scale to zero

Other:
  -y, --non-interactive     Never prompt; fail if a required value is missing
  -h, --help                Show this help

Examples:
  ./setup.sh                                    # admin, interactive
  ./setup.sh --developer \
      --project acme-prod \
      --endpoint https://claude-otel-collector-abc.us-central1.run.app -y
EOF
}

# Seed defaults from the example, then let an existing config.env override them.
# shellcheck disable=SC1090
source "${EXAMPLE}"
HAD_CONFIG=0
if [[ -f "${CONFIG}" ]]; then
  # shellcheck disable=SC1090
  source "${CONFIG}"
  HAD_CONFIG=1
fi
# Seed the endpoint default from a previous run, if any.
ENDPOINT=""
[[ -s "${URL_FILE}" ]] && ENDPOINT="$(cat "${URL_FILE}")"

# ---- Parse flags ------------------------------------------------------------
DEVELOPER_MODE=0
INTERACTIVE=1
FROM_FLAG=""   # space-separated list of var names supplied on the command line

mark() { FROM_FLAG="${FROM_FLAG} $1"; }
given() { [[ " ${FROM_FLAG} " == *" $1 "* ]]; }

need_value() {
  [[ $# -ge 2 ]] || { echo "setup: $1 requires a value" >&2; exit 2; }
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --developer)        DEVELOPER_MODE=1; shift ;;
    -y|--non-interactive) INTERACTIVE=0; shift ;;
    -h|--help)          usage; exit 0 ;;
    --project)          need_value "$1" "${2:-}"; PROJECT="$2";         mark PROJECT;         shift 2 ;;
    --endpoint)         need_value "$1" "${2:-}"; ENDPOINT="$2";        mark ENDPOINT;        shift 2 ;;
    --region)           need_value "$1" "${2:-}"; REGION="$2";          mark REGION;          shift 2 ;;
    --service)          need_value "$1" "${2:-}"; SERVICE="$2";         mark SERVICE;         shift 2 ;;
    --runtime-sa)       need_value "$1" "${2:-}"; RUNTIME_SA_NAME="$2"; mark RUNTIME_SA_NAME; shift 2 ;;
    --invoker-sa)       need_value "$1" "${2:-}"; INVOKER_SA_NAME="$2"; mark INVOKER_SA_NAME; shift 2 ;;
    --developers)       need_value "$1" "${2:-}"; DEVELOPERS="$2";      mark DEVELOPERS;      shift 2 ;;
    --invoker-members)  need_value "$1" "${2:-}"; INVOKER_MEMBERS="$2"; mark INVOKER_MEMBERS; shift 2 ;;
    --min-instances)    need_value "$1" "${2:-}"; MIN_INSTANCES="$2";   mark MIN_INSTANCES;   shift 2 ;;
    *) echo "setup: unknown argument '$1' (try --help)" >&2; exit 2 ;;
  esac
done

# No tty and no explicit -y? Prompting would fail with `read` hitting EOF, which
# previously killed the script under `set -e` with no diagnostic at all.
if [[ ${INTERACTIVE} -eq 1 && ! -t 0 ]]; then
  INTERACTIVE=0
  NO_TTY=1
else
  NO_TTY=0
fi

# ask VAR "Prompt text" [required] — prompts for the named variable, showing its
# current value as the default (Enter keeps it). Skipped if the value came from a
# flag. When not interactive, a missing required value is a hard error.
ask() {
  local var="$1" prompt="$2" required="${3:-0}" current="${!1:-}" answer

  if given "${var}"; then
    return 0
  fi

  if [[ ${INTERACTIVE} -eq 0 ]]; then
    if [[ ${required} -eq 1 && -z "${current}" ]]; then
      echo "setup: ${var} is required but was not supplied." >&2
      if [[ ${NO_TTY} -eq 1 ]]; then
        echo "  (stdin is not a terminal, so there is nothing to prompt on.)" >&2
      fi
      echo "  Pass it as a flag — see ./setup.sh --help" >&2
      exit 1
    fi
    return 0
  fi

  read -r -p "${prompt} [${current}]: " answer || {
    echo >&2
    echo "setup: input ended unexpectedly; nothing was written." >&2
    exit 1
  }
  printf -v "${var}" '%s' "${answer:-${current}}"
}

if [[ ${DEVELOPER_MODE} -eq 1 ]]; then
  echo "== Claude Code OTel Collector — developer setup =="
else
  echo "== Claude Code OTel Collector — admin setup =="
fi
[[ ${HAD_CONFIG} -eq 1 ]] && echo "==> Found existing config.env; its values are the defaults below."
echo

ask PROJECT "GCP project ID (required)" 1
while [[ -z "${PROJECT}" ]]; do
  echo "  PROJECT is required."
  ask PROJECT "GCP project ID (required)" 1
done

if [[ ${DEVELOPER_MODE} -eq 1 ]]; then
  # A developer needs exactly two things beyond the project: the endpoint (token
  # audience + OTLP destination) and the invoker SA name they impersonate.
  # Everything else stays at its default and is never used off the admin path.
  ask ENDPOINT        "Collector URL from your admin (required)" 1
  ask INVOKER_SA_NAME "Shared invoker SA name (as configured by your admin)"
  while [[ -z "${ENDPOINT}" ]]; do
    echo "  The collector URL is required — ask your admin for it."
    ask ENDPOINT      "Collector URL from your admin (required)" 1
  done
else
  ask REGION          "Cloud Run region"
  ask SERVICE         "Cloud Run service name"
  ask RUNTIME_SA_NAME "Collector runtime SA name"
  ask INVOKER_SA_NAME "Shared invoker SA name (laptop devs impersonate this)"
  ask DEVELOPERS      "Who may send telemetry (prefer domain:yourco.com or group:team@yourco.com, so you never list individuals)"
  ask INVOKER_MEMBERS "Direct-invoker machines' SAs (metadata path; serviceAccount:...)"
  ask MIN_INSTANCES   "Min instances (1 = always-on capture, 0 = scale to zero)"
fi

# ---- Validate ---------------------------------------------------------------
if [[ -n "${ENDPOINT}" && "${ENDPOINT}" != https://* ]]; then
  echo "setup: --endpoint must be an https:// URL (got '${ENDPOINT}')." >&2
  echo "  It is used verbatim as the ID-token audience; a mismatch means 401/403." >&2
  exit 1
fi
# A trailing slash changes the audience string and silently breaks auth.
ENDPOINT="${ENDPOINT%/}"

# Keep the less-frequently-changed knobs at their sourced values.
cat > "${CONFIG}" <<EOF
# Written by setup.sh. Gitignored — safe to hold real values.

# ---- Required ---------------------------------------------------------------
PROJECT="${PROJECT}"

# ---- Cloud Run service ------------------------------------------------------
REGION="${REGION}"
SERVICE="${SERVICE}"
SECRET="${SECRET}"
CONTAINER_PORT="${CONTAINER_PORT}"
IMAGE="${IMAGE}"
MIN_INSTANCES="${MIN_INSTANCES}"
MAX_INSTANCES="${MAX_INSTANCES}"
MEMORY="${MEMORY}"

# ---- Identities -------------------------------------------------------------
RUNTIME_SA_NAME="${RUNTIME_SA_NAME}"
INVOKER_SA_NAME="${INVOKER_SA_NAME}"
DEVELOPERS="${DEVELOPERS}"
INVOKER_MEMBERS="${INVOKER_MEMBERS}"
EOF
echo
echo "==> Wrote ${CONFIG}"

if [[ -n "${ENDPOINT}" ]]; then
  printf '%s' "${ENDPOINT}" > "${URL_FILE}"
  echo "==> Wrote ${URL_FILE##*/} (${ENDPOINT})"
fi

# ---- Render SQL templates ---------------------------------------------------
for tpl in daily-spend.sql spend-by-model.sql; do
  src="${SCRIPT_DIR}/${tpl}"
  out="${SCRIPT_DIR}/${tpl%.sql}.local.sql"
  [[ -f "${src}" ]] || continue
  sed "s/YOUR_PROJECT_ID/${PROJECT}/g" "${src}" > "${out}"
  echo "==> Rendered ${out##*/}"
done

echo
if [[ ${DEVELOPER_MODE} -eq 1 ]]; then
  echo "Next: ./print-settings.sh you@yourcompany.com --merge"
  echo "      (writes ~/.claude/settings.json, then restart Claude Code)"
else
  echo "Next: ./deploy.sh   (provisions APIs, SAs, IAM, secret, and the Cloud Run service)"
fi
