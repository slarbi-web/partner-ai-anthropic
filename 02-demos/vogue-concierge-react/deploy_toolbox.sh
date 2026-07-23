#!/bin/bash
# Copyright 2026 slarbi-web
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

# Vogue Concierge — deploy the MCP Toolbox to Cloud Run.
#
# The toolbox exposes the BigQuery inventory/loyalty tools that the deployed
# agents call. deploy_agent_engine.py reads TOOLBOX_URL from the environment, so
# run this FIRST, then export the printed TOOLBOX_URL before deploying the agents.
# (bootstrap.sh does this chaining for you.)
#
# toolbox/tools.yaml ships with a `your-gcp-project-id` placeholder. We render a
# concrete copy into an isolated build context so the committed template stays
# clean and your real project id is never written back into the repo.
#
# NOTE: --allow-unauthenticated makes the tools reachable by the Google-managed
# Agent Runtime runtime without extra IAM wiring, which is fine for a demo/
# workshop. For production, front the toolbox with authenticated invocation.
# [GAP — needs auth for production]

set -e

PROJECT_ID="${VERTEXAI_PROJECT:?Set VERTEXAI_PROJECT to your GCP project id (see README)}"
PROJECT_NUMBER="${PROJECT_NUMBER:?Set PROJECT_NUMBER to your GCP project number (see README)}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="vogue-toolbox"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo "=== Vogue Concierge — MCP Toolbox Deploy ==="
echo "Project: ${PROJECT_ID} | Region: ${REGION} | Service: ${SERVICE_NAME}"
echo ""

# Render tools.yaml with the real project id into a throwaway build context so we
# never mutate the committed template (which keeps the your-gcp-project-id
# placeholder for the public repo).
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "${BUILD_DIR}"' EXIT
cp toolbox/Dockerfile "${BUILD_DIR}/Dockerfile"
sed "s/your-gcp-project-id/${PROJECT_ID}/g" toolbox/tools.yaml > "${BUILD_DIR}/tools.yaml"

echo "Step 1: Building toolbox image..."
gcloud builds submit "${BUILD_DIR}" --tag "${IMAGE}" --project "${PROJECT_ID}" --timeout=600

# The toolbox container listens on 5000 (see toolbox/Dockerfile), so tell Cloud
# Run to route to that port. It authenticates to BigQuery with the runtime
# service account's ADC, so that SA needs BigQuery Data Viewer + Job User (the
# bootstrap grants these).
echo ""
echo "Step 2: Deploying toolbox to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --platform managed \
  --allow-unauthenticated \
  --port 5000 \
  --memory 512Mi \
  --cpu 1 \
  --timeout 60 \
  --min-instances 0 \
  --max-instances 3 \
  --service-account "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

URL=$(gcloud run services describe "${SERVICE_NAME}" \
  --region "${REGION}" --project "${PROJECT_ID}" --format "value(status.url)")

echo ""
echo "=========================================="
echo "MCP Toolbox deployed."
echo "Bridge the agents to it by exporting this before deploy_agent_engine.py:"
# Machine-readable line — bootstrap.sh greps for '^TOOLBOX_URL='.
echo "TOOLBOX_URL=${URL}"
echo "=========================================="
