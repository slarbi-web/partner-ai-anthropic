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

"""Vogue Concierge — configuration.

This is the single place to change *which* Claude models power *which* agent, and
where the supporting data services (RAG corpus, BigQuery, MCP Toolbox) live.

KEY IDEA: "the right model for the right agent."
------------------------------------------------
Rather than one model doing everything, each agent is matched to a Claude model
that fits its job. You can retune the whole system by editing the four model
constants below (or by setting the matching environment variables at deploy
time — handy for A/B testing models without changing code).

AUTH NOTE
---------
Claude runs on Vertex AI and authenticates with Google Application Default
Credentials (ADC). There is no Anthropic API key here — the previous per-request
token-refresh plumbing (refreshing an ``OPENAI_API_KEY`` every turn) is gone.
"""

import os


# --- GCP project / regions -------------------------------------------------
# The project that owns Agent Engine, BigQuery, GCS, and the RAG corpus.
# Required — set VERTEXAI_PROJECT so nothing ever runs against the wrong project.
PROJECT_ID = os.environ.get("VERTEXAI_PROJECT")
if not PROJECT_ID:
    raise RuntimeError(
        "VERTEXAI_PROJECT is not set. Copy .env.example to .env (or export it) and set "
        "it to your GCP project id. See the README 'Setup' section."
    )

# Region for Agent Engine / BigQuery / Cloud Run.
REGION = "us-central1"

# Region that actually SERVES Claude on Agent Platform. Claude availability AND quota are
# region-specific. We default to "global", which routes to available capacity and
# is the recommended default for Claude on Agent Platform — in this project all four
# models (Sonnet 4.6, Opus 4.8, Haiku 4.5) have quota there, whereas regional
# endpoints like "us-east5" had zero Opus 4.8 quota until a quota increase is
# granted. Override per your own project's Model Garden / quota setup.
CLAUDE_VERTEX_REGION = os.environ.get("CLAUDE_VERTEX_REGION", "global")


# --- The right model for the right agent ------------------------------------
# Bare Vertex model ids for current-generation Claude. If your Model Garden
# requires a dated snapshot, override via env, e.g. "claude-opus-4-8@<date>".
#
#   Orchestrator  -> Sonnet 4.6 : fast, excellent tool routing + concierge voice
#   Style Advisor -> Opus 4.8   : deepest taste / trend reasoning (the showpiece)
#   Inventory     -> Haiku 4.5  : fast, cheap, deterministic stock/price lookups
#   Returns/Care  -> Haiku 4.5  : quick policy answers + empathetic care advice
ORCHESTRATOR_MODEL = os.environ.get("ORCHESTRATOR_MODEL", "claude-sonnet-4-6")
STYLE_MODEL = os.environ.get("STYLE_MODEL", "claude-opus-4-8")
INVENTORY_MODEL = os.environ.get("INVENTORY_MODEL", "claude-haiku-4-5")
RETURNS_MODEL = os.environ.get("RETURNS_MODEL", "claude-haiku-4-5")
#   Checkout      -> Haiku 4.5  : structured order flow (the money math lives in
#                                 the place_order tool, so the model just gathers
#                                 details, calls one tool, and reads back the result)
CHECKOUT_MODEL = os.environ.get("CHECKOUT_MODEL", "claude-haiku-4-5")

# Reasoning effort for the Style Advisor only. Opus 4.8 supports
# low|medium|high|xhigh|max; "high" buys richer outfit reasoning for a modest
# latency cost. The other agents leave effort unset for snappy responses.
STYLE_EFFORT = os.environ.get("STYLE_EFFORT", "high")


# --- Data plane (unchanged from the original build) -------------------------
# BigQuery dataset holding inventory + loyalty tables (queried via MCP Toolbox).
BQ_DATASET = "vogue_concierge"

# GCS bucket with the 30 Imagen-generated product images.
GCS_BUCKET = f"{PROJECT_ID}-vogue-concierge"

# Vertex AI RAG corpus (catalog + trend report). Note it lives in its own region.
RAG_REGION = "us-west1"
RAG_CORPUS_DISPLAY_NAME = "vogue-concierge-catalog"
RAG_CORPUS_RESOURCE = os.environ.get(
    "RAG_CORPUS_RESOURCE",
    "projects/YOUR_PROJECT_NUMBER/locations/us-west1/ragCorpora/YOUR_RAG_CORPUS_ID",
)

# MCP Toolbox endpoint that exposes the BigQuery inventory/loyalty tools.
# Defaults to localhost for dev; set TOOLBOX_URL to the Cloud Run URL in prod.
TOOLBOX_URL = os.environ.get("TOOLBOX_URL", "http://localhost:5000")
