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

"""Vogue Concierge — multi-agent definition (Claude on Agent Platform + Google ADK).

THE TEAM
--------
Instead of one model doing everything, we build a small team of ADK agents, each
on the Claude model that best fits its job (see ``agents/config.py``):

    vogue_concierge (Orchestrator, Sonnet 4.6)
      ├─ style_advisor        (Opus 4.8)   outfit / trend / recommendation
      ├─ inventory_specialist (Haiku 4.5)  stock / sizes / price / loyalty
      └─ returns_expert       (Haiku 4.5)  returns / exchanges / care

HOW DELEGATION WORKS (AgentTool)
--------------------------------
We wrap each specialist as an ADK ``AgentTool`` and hand those tools to the
orchestrator. From the orchestrator's point of view a specialist is "just a
tool": it calls the tool, the specialist runs its own model + its own tools, and
returns an answer the orchestrator relays to the customer. The orchestrator
keeps one consistent concierge voice throughout — it never hands the microphone
over, it delegates and reports back.

This pattern only became practical once we moved to Claude: the previous model
could not transfer between agents reliably, which is why the old build crammed
all three personas into a single agent. Claude's reliable tool-calling lets us
use the cleaner team structure.

WHERE THIS RUNS
---------------
``root_agent`` is the entry point Vertex AI Agent Engine (the agent runtime)
loads and serves. ``deploy_agent_engine.py`` packages this module + data and
deploys it. Locally you can also run ``adk web agents`` to chat with it.
"""

from google.adk.agents import Agent

# AgentTool turns a full Agent into a callable tool for a parent agent — this is
# the mechanism behind orchestrator -> specialist delegation.
from google.adk.tools.agent_tool import AgentTool

from . import config
from . import prompt
from .claude_model import ClaudeVertexModel

# Tools the specialists use. catalog_search / trend_search query the Vertex RAG
# corpus (with a local keyword fallback); the inventory tools query BigQuery via
# the MCP Toolbox (with a local mock fallback).
from .tools.catalog_search import catalog_search
from .tools.trend_rag import trend_search
from .tools.order_tools import (
    place_order, quote_order, check_loyalty, check_stock, get_order, enroll_loyalty,
)


def _load_inventory_tools() -> list:
    """Return the inventory/loyalty tools for the Inventory Specialist.

    Preference order:
      1. MCP Toolbox tools (real BigQuery queries) if the Toolbox server is
         reachable at config.TOOLBOX_URL.
      2. Local Python fallbacks (mocked data) so the agent still works in dev
         without BigQuery or the Toolbox running.
    """
    try:
        from toolbox_core import ToolboxSyncClient

        toolbox = ToolboxSyncClient(config.TOOLBOX_URL)
        tools = toolbox.load_toolset("default")
        print(f"Loaded {len(tools)} MCP Toolbox tools from {config.TOOLBOX_URL}")
        return list(tools)
    except Exception as e:  # noqa: BLE001 - dev convenience fallback
        print(f"MCP Toolbox unavailable ({e}); using local fallback inventory tools")
        from .tools.toolbox_tools import check_inventory, get_loyalty_discount

        return [check_inventory, get_loyalty_discount]


def create_agent() -> Agent:
    """Build and return the orchestrator with its three specialist sub-agents.

    We build the specialists first, then wrap them as tools for the orchestrator.
    Each ``Agent`` carries: a name (how it's addressed), its own ClaudeVertexModel
    (the right model for the job), a short description (the orchestrator reads
    these to choose whom to delegate to), an instruction (its system prompt), and
    its tools.
    """

    # --- Specialist 1: Style Advisor (Opus 4.8, high effort) ---------------
    style_advisor = Agent(
        name="style_advisor",
        model=ClaudeVertexModel(model=config.STYLE_MODEL, effort=config.STYLE_EFFORT),
        description=(
            "Expert fashion stylist. Handles outfit ideas, trend questions, "
            "product recommendations, and styling advice."
        ),
        instruction=prompt.STYLE_PROMPT,
        tools=[catalog_search, trend_search],
    )

    # --- Specialist 2: Product & Pricing (Haiku 4.5) ----------------------
    # NOTE: live STOCK no longer comes from this agent. The previous build used a
    # mock `check_inventory` (random per-size numbers) because the engine can't
    # reach BigQuery — that produced fake, inconsistent stock. Real stock now flows
    # through the top-level `check_stock` signal, which the Cloud Run layer answers
    # from the real BigQuery `inventory` table (same source checkout validates
    # against). So this specialist focuses on factual product details and pricing
    # from the catalog; it keeps `catalog_search` to resolve names/SKUs and specs.
    inventory_specialist = Agent(
        name="inventory_specialist",
        model=ClaudeVertexModel(model=config.INVENTORY_MODEL),
        description=(
            "Answers factual product questions — pricing, materials, colours, and "
            "product details — from the catalog. (Live stock is handled separately.)"
        ),
        instruction=prompt.INVENTORY_PROMPT,
        tools=[catalog_search],
    )

    # --- Specialist 3: Returns & Care (Haiku 4.5) -------------------------
    returns_expert = Agent(
        name="returns_expert",
        model=ClaudeVertexModel(model=config.RETURNS_MODEL),
        description=(
            "Handles returns, exchanges, order issues, and garment-care advice."
        ),
        instruction=prompt.RETURNS_PROMPT,
        # catalog_search lets it look up a product's material for care tips.
        tools=[catalog_search],
    )

    # --- Orchestrator (Sonnet 4.6) ----------------------------------------
    # The three specialists become tools here. Checkout is different: `place_order`
    # is a TOP-LEVEL tool on the orchestrator (not a sub-agent) so its call — with
    # clean args (sku, size, customer_id, quantity) — is visible in the Agent
    # Engine event stream. The Cloud Run layer reads that call and runs the REAL
    # BigQuery checkout (see checkout.py), because the engine can't reach BigQuery.
    orchestrator = Agent(
        name="vogue_concierge",
        model=ClaudeVertexModel(model=config.ORCHESTRATOR_MODEL),
        description="Vogue Concierge — the lead AI concierge of a luxury boutique.",
        instruction=prompt.ORCHESTRATOR_PROMPT,
        tools=[
            AgentTool(agent=style_advisor),
            AgentTool(agent=inventory_specialist),
            AgentTool(agent=returns_expert),
            check_stock,    # signal -> Cloud Run reads real BigQuery inventory
            quote_order,    # signal -> Cloud Run computes the confirm-to-pay summary
            place_order,    # signal -> Cloud Run charges + records the order
            get_order,      # signal -> Cloud Run reads a stored order by number
            check_loyalty,  # signal -> Cloud Run reads real BigQuery loyalty
            enroll_loyalty, # signal -> Cloud Run creates a new loyalty member
        ],
    )

    # Stamp a version so deployments are easy to tell apart. ``object.__setattr__``
    # bypasses pydantic's field validation on the Agent model.
    object.__setattr__(orchestrator, "version", "2.0.0")
    return orchestrator


# Vertex AI Agent Engine and ``adk web`` both look for a module-level
# ``root_agent``. This is the single entry point to the whole team.
root_agent = create_agent()
