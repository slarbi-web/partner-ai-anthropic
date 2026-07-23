# Vogue Concierge — AI Boutique (Claude on Agent Platform, React edition)

An elite AI fashion concierge built with **Claude models on Agent Platform**,
**Google ADK**, and **Vertex AI Agent Engine**, fronted by a **React**
storefront.

This is an Anthropic + Google "better together" build: Claude does the reasoning,
Google Cloud provides the runtime (Agent Engine), retrieval (Vertex RAG), and
structured data (BigQuery).

> **This repo is the React-only build.** It serves the concierge through a React
> UI and a FastAPI relay. A separate build adds a **Gemini Enterprise** surface
> (A2A + inline A2UI cards) on top of the same agent team.

## Quickstart — one command

The fastest path is the interactive installer. It deploys the whole thing into
**your own** Google Cloud project: it collects your config, enables APIs, seeds
the data plane (Imagen catalog, BigQuery, RAG), and deploys the MCP Toolbox, the
agent team, and the React UI — capturing every generated id into `.env` as it goes.

[![Open in Cloud Shell](https://gstatic.com/cloudssh/images/open-btn.svg)](https://console.cloud.google.com/cloudshell/open?cloudshell_git_repo=https://github.com/slarbi-web/vogue-concierge-react.git&cloudshell_git_branch=main&cloudshell_workspace=.&cloudshell_tutorial=README.md)

```bash
# In Cloud Shell (or any machine with gcloud + python3), from the repo root:
./bootstrap.sh
```

The installer is **interactive and safe to re-run** — each step is idempotent, so
if something fails (a missing model, a quota bump) you fix it and run it again to
resume. The one thing it can't automate and will pause to walk you through is
enabling the Claude + Imagen models in Vertex AI Model Garden.

Prefer to run the steps yourself? See [Manual setup](#manual-setup) below.

## Architecture

![Vogue Concierge Agent Architecture](docs/architecture.png)

> For a detailed technical deep dive into the multi-agent design, tools, and data flows, see [**docs/ARCHITECTURE.md**](docs/ARCHITECTURE.md).

```
React UI ──FastAPI (Cloud Run)──▶  Vertex AI Agent Engine
                                        │
                                Orchestrator  (Claude Sonnet 4.6)
                                   ├─ Style Advisor       (Claude Opus 4.8)
                                   ├─ Inventory & Pricing  (Claude Haiku 4.5)
                                   └─ Returns & Care        (Claude Haiku 4.5)
                                        │
                          Vertex RAG · BigQuery (MCP Toolbox) · Catalog (GCS)
```

The **React UI** talks to `app.py` (REST/SSE) on Cloud Run, which relays to the
agent team on Agent Engine.

## The right model for the right agent

Each agent runs on the Claude model that fits its job (configured in
`agents/config.py`, overridable by env var):

| Agent | Model | Why |
|---|---|---|
| Orchestrator / Concierge | `claude-sonnet-4-6` | Fast, excellent tool routing + a consistent concierge voice |
| Style Advisor | `claude-opus-4-8` | Deepest taste and trend reasoning (the showpiece), `effort=high` |
| Inventory & Pricing | `claude-haiku-4-5` | Fast, cheap, deterministic stock/price lookups |
| Returns & Care | `claude-haiku-4-5` | Quick policy answers and garment-care advice |
| Checkout | `claude-haiku-4-5` | Structured order flow (money math lives in the checkout tool) |

Claude runs on Vertex AI via `AnthropicVertex`, authenticated with Google
Application Default Credentials — **there is no Anthropic API key**.

## Prerequisites

You deploy this into **your own** Google Cloud project. Nothing here ships with
working defaults — the scripts fail fast until you configure the values below.

**1. Google Cloud project**
- A GCP project with **billing enabled**, and the `gcloud` CLI installed and
  initialized (`gcloud init`).
- Note your **project id** and **project number**
  (`gcloud projects describe <id> --format="value(projectNumber)"`).

**2. Enable APIs**
```bash
gcloud services enable \
  aiplatform.googleapis.com \
  bigquery.googleapis.com \
  storage.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  --project "$VERTEXAI_PROJECT"
```

**3. Claude on Agent Platform (Model Garden + quota)**
- In **Vertex AI → Model Garden**, enable each Claude model you use: **Sonnet
  4.6**, **Opus 4.8**, and **Haiku 4.5**.
- Confirm you have **quota** for them. `CLAUDE_VERTEX_REGION=global` routes to
  available capacity and is recommended; regional endpoints may lack Opus 4.8
  quota until you request an increase.

**4. Imagen 3 (required)**
- `scripts/setup_catalog.py` generates the 30 product images with **Imagen 3**
  on Vertex AI. Imagen must be **enabled/allowlisted** for your project. Without
  it, catalog setup will not complete — this is a hard requirement, not optional.

**5. Authentication & IAM**
- `gcloud auth application-default login` (ADC — no API keys anywhere).
- The identity running setup/deploy needs, at minimum: **Vertex AI User**,
  **Storage Admin**, **BigQuery Admin**, **Cloud Run Admin**, **Cloud Build
  Editor**, and **Service Account User**. The Agent Engine / Cloud Run runtime
  service account (`<project-number>-compute@developer.gserviceaccount.com`)
  needs **Vertex AI User** and **BigQuery Data Editor/User**.

**6. Local tooling**
- **Python 3.11+**, **Node.js 20+** (for the React UI), **Docker**, and the
  **MCP Toolbox** binary (`scripts/start_toolbox.sh` fetches it for local runs).

## Manual setup

`./bootstrap.sh` (see [Quickstart](#quickstart--one-command)) runs all of this for
you. Do it by hand if you want step-by-step control. Run from the repo root; the
scripts read configuration from environment variables, so export your `.env` first.

```bash
# 0. Configure — copy the template, fill in your values, and export them.
cp .env.example .env
#   edit .env: set VERTEXAI_PROJECT and PROJECT_NUMBER (at least)
set -a; source .env; set +a

# 1. Verify Claude is reachable on Vertex in your project
python tests/test_connection.py

# 2. Build the data plane (order matters)
python scripts/setup_catalog.py     # Imagen images -> GCS, rewrites data/products.json
python scripts/setup_bigquery.py    # inventory + loyalty tables from the catalog
python scripts/setup_orders.py      # append-only orders table
python scripts/setup_rag.py         # RAG corpus; PRINTS RAG_CORPUS_RESOURCE=<name>
#   -> copy the printed resource into .env as RAG_CORPUS_RESOURCE, then re-export:
set -a; source .env; set +a

# 3. Deploy the MCP Toolbox to Cloud Run (the deployed agents call it for the
#    BigQuery inventory/loyalty tools). PRINTS TOOLBOX_URL=<url>.
./deploy_toolbox.sh
#   -> set TOOLBOX_URL in .env, then re-export:
set -a; source .env; set +a

# 4. (Optional) run the agents locally with the ADK dev UI
adk web agents

# 5. Deploy the agent team to Agent Engine (needs RAG_CORPUS_RESOURCE + TOOLBOX_URL)
python deploy_agent_engine.py       # PRINTS AGENT_ENGINE_ID=<id>
#   -> set AGENT_ENGINE_ID in .env, then re-export
set -a; source .env; set +a

# 6. Deploy the React UI (Cloud Run)
./deploy.sh
```

`deploy.sh` and `deploy_toolbox.sh` require `VERTEXAI_PROJECT` and `PROJECT_NUMBER`
in the environment (the UI deploy also needs `AGENT_ENGINE_ID`) and will refuse to
run without them. When `deploy.sh` finishes it prints the public URL of your
storefront.

## How it's built

- **`agents/claude_model.py`** — `ClaudeVertexModel(BaseLlm)`: the adapter that
  plugs Claude-on-Vertex into Google ADK. Converts ADK requests to the Anthropic
  Messages API and back, with native tool-calling. This is the heart of the
  Claude integration and the file to lift into your own ADK project.
- **`agents/agent.py`** — the team: an orchestrator that delegates to specialists
  via ADK `AgentTool`.
- **`agents/prompt.py`** — one system prompt per agent.
- **`checkout.py`** — the real BigQuery checkout (loyalty discount → simulated
  payment → write order → credit points), run on the Cloud Run layer because the
  Agent Engine sandbox can't reach BigQuery (the "signal/execute" pattern).
- **`app.py`** — FastAPI server for the React UI; relays chat to Agent Engine.

## Project structure

```
vogue-concierge/
├── agents/                  # ADK agent team (runs on Agent Engine)
│   ├── claude_model.py      # ClaudeVertexModel — Claude-on-Vertex ADK adapter
│   ├── agent.py             # orchestrator + specialists (AgentTool)
│   ├── config.py            # model-per-agent mapping + data config
│   ├── prompt.py            # one prompt per agent
│   └── tools/               # catalog_search, trend_rag, order/inventory tools
├── toolbox/                 # MCP Toolbox config (BigQuery inventory/loyalty)
├── data/                    # products.json (30 items) + trend_report.md
├── scripts/                 # one-time data setup (catalog, RAG, BigQuery, orders)
├── ui/                      # Next.js / React storefront
├── app.py                   # FastAPI server for the React UI
├── checkout.py              # real BigQuery checkout (Cloud Run layer)
├── bootstrap.sh             # interactive one-command installer
├── deploy_agent_engine.py   # deploy agents to Agent Engine
├── deploy_toolbox.sh        # deploy the MCP Toolbox to Cloud Run
└── deploy.sh                # deploy the UI to Cloud Run
```

## License

Apache License 2.0 — see [LICENSE](LICENSE). Copyright 2026 slarbi-web.
