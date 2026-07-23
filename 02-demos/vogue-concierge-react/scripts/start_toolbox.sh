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

# Vogue Concierge — Start MCP Toolbox for BigQuery
# Pre-applied Fix 20: Valid tools.yaml with correct schema
# Pre-applied Fix 21: Uses custom binary approach from V1

set -e

PROJECT_ID="${VERTEXAI_PROJECT:?Set VERTEXAI_PROJECT to your GCP project id (see README)}"
TOOLBOX_PORT=5000

echo "=== Starting MCP Toolbox ==="
echo "Config: toolbox/tools.yaml"
echo "Port: ${TOOLBOX_PORT}"
echo ""

# Check if toolbox binary exists
if command -v toolbox &> /dev/null; then
    echo "Using installed toolbox binary..."
    toolbox --tools_file toolbox/tools.yaml --port ${TOOLBOX_PORT}
elif [ -f "./toolbox" ]; then
    echo "Using local toolbox binary..."
    ./toolbox --tools_file toolbox/tools.yaml --port ${TOOLBOX_PORT}
else
    echo "Toolbox binary not found. Downloading..."
    # Download from GCS (v0.28.0 — known working from V1)
    gsutil cp gs://cloud-sql-connectors/toolbox/v0.28.0/toolbox-linux-amd64 ./toolbox
    chmod +x ./toolbox
    echo "Downloaded toolbox v0.28.0"
    ./toolbox --tools_file toolbox/tools.yaml --port ${TOOLBOX_PORT}
fi
