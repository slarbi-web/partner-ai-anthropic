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

"""Deploy Vogue Concierge to Vertex AI Agent Engine (the agent runtime).

WHAT THIS DOES
--------------
Packages the ``agents/`` team + the ``data/`` catalog and uploads it to Vertex AI
Agent Engine, which then hosts and serves the multi-agent system for you (no
servers to run). The FastAPI app (``app.py``) and the A2A bridge both call this
deployed engine via ``stream_query``.

PREREQUISITES (one-time, in your own project)
---------------------------------------------
  * Enable the Claude models in Vertex AI Model Garden for your project + the
    region in CLAUDE_VERTEX_REGION (see agents/config.py).
  * Create the staging bucket below (or point STAGING_BUCKET at an existing one).
  * Have Application Default Credentials available (gcloud auth or a deploy SA).

RUN
---
    python deploy_agent_engine.py

It prints the new Agent Engine ID; set that as AGENT_ENGINE_ID on the Cloud Run
service that serves the UI (the printed gcloud command does this for you).
"""

import os

import vertexai
from vertexai import agent_engines
from vertexai.preview import reasoning_engines

PROJECT_ID = os.environ.get("VERTEXAI_PROJECT")
if not PROJECT_ID:
    raise RuntimeError(
        "VERTEXAI_PROJECT is not set. Export it (or copy .env.example to .env) with your "
        "GCP project id before deploying. See the README 'Setup' section."
    )
LOCATION = os.environ.get("AGENT_ENGINE_LOCATION", "us-central1")
STAGING_BUCKET = os.environ.get("STAGING_BUCKET", f"gs://{PROJECT_ID}-vogue-staging")

# The RAG corpus resource printed by scripts/setup_rag.py — required so the deployed
# agents can ground catalog/trend answers. The MCP Toolbox URL comes from deploying
# toolbox/ to Cloud Run (defaults to a local toolbox).
RAG_CORPUS_RESOURCE = os.environ.get("RAG_CORPUS_RESOURCE")
if not RAG_CORPUS_RESOURCE:
    raise RuntimeError(
        "RAG_CORPUS_RESOURCE is not set. Run scripts/setup_rag.py first (it prints the "
        "corpus resource name), then export it or add it to .env before deploying."
    )
TOOLBOX_URL = os.environ.get("TOOLBOX_URL", "http://localhost:5000")

vertexai.init(
    project=PROJECT_ID,
    location=LOCATION,
    staging_bucket=STAGING_BUCKET,
)

# Import after vertexai.init so the agent builds in the right context.
from agents.agent import create_agent

agent = create_agent()

# AdkApp wraps the ADK agent so Agent Engine can serve it. Tracing on gives you
# per-turn spans in Cloud Trace, which pairs nicely with the A2A timeline view.
app = reasoning_engines.AdkApp(agent=agent, enable_tracing=True)

# Runtime dependencies installed inside the Agent Engine container. This must
# include anthropic[vertex] so ClaudeVertexModel can reach Claude on Agent Platform, and
# google-cloud-bigquery so the place_order tool can write orders.
REQUIREMENTS = [
    "google-cloud-aiplatform[adk,agent_engines]",
    "anthropic[vertex]>=0.40.0",
    "httpx>=0.27.0",
    "toolbox-core>=0.1.0",
    "google-cloud-bigquery>=3.25.0",
    "google-cloud-storage>=3.0.0",
]
# Environment for the deployed agents. No API key — Claude on Agent Platform uses the
# Agent Engine service account's ADC. Override model ids / regions here if your
# Model Garden uses different ones.
ENV_VARS = {
    "VERTEXAI_PROJECT": PROJECT_ID,
    # "global" has quota for all four models in this project; regional endpoints
    # needed an Opus 4.8 quota increase. Change if your quota differs.
    "CLAUDE_VERTEX_REGION": "global",
    "ORCHESTRATOR_MODEL": "claude-sonnet-4-6",
    "STYLE_MODEL": "claude-opus-4-8",
    "INVENTORY_MODEL": "claude-haiku-4-5",
    "RETURNS_MODEL": "claude-haiku-4-5",
    "CHECKOUT_MODEL": "claude-haiku-4-5",
    "TOOLBOX_URL": TOOLBOX_URL,
    "RAG_CORPUS_RESOURCE": RAG_CORPUS_RESOURCE,
}
EXTRA_PACKAGES = ["agents/", "data/"]  # ship the agent package + catalog data

# UPDATE IN PLACE vs CREATE NEW: if AGENT_ENGINE_ID is set we update the existing
# engine (keeping the same ID, so the UI and A2A bridge don't need re-pointing).
# Otherwise we create a fresh engine. This avoids accumulating orphaned engines.
PROJECT_NUMBER = os.environ.get("PROJECT_NUMBER", "YOUR_PROJECT_NUMBER")
EXISTING_ID = os.environ.get("AGENT_ENGINE_ID", "").strip()

if EXISTING_ID:
    resource = f"projects/{PROJECT_NUMBER}/locations/{LOCATION}/reasoningEngines/{EXISTING_ID}"
    print(f"Updating existing Agent Engine {EXISTING_ID} in place...")
    remote_app = agent_engines.update(
        resource_name=resource,
        agent_engine=app,
        requirements=REQUIREMENTS,
        env_vars=ENV_VARS,
        extra_packages=EXTRA_PACKAGES,
    )
else:
    print("Creating a new Agent Engine...")
    remote_app = agent_engines.create(
        agent_engine=app,
        requirements=REQUIREMENTS,
        env_vars=ENV_VARS,
        extra_packages=EXTRA_PACKAGES,
    )

agent_engine_id = remote_app.gca_resource.name.split("/")[-1]
print(f"\n{'=' * 60}")
print("Agent Engine deployed.")
print(f"Resource:        {remote_app.gca_resource.name}")
print(f"Agent Engine ID: {agent_engine_id}")
print(f"{'=' * 60}")
# Machine-readable line — bootstrap.sh greps for '^AGENT_ENGINE_ID='.
print(f"AGENT_ENGINE_ID={agent_engine_id}")
print("\nPoint the Cloud Run UI service at the new engine:")
print(
    f'gcloud run services update vogue-concierge --region {LOCATION} '
    f'--project {PROJECT_ID} --set-env-vars "AGENT_ENGINE_ID={agent_engine_id}"'
)
