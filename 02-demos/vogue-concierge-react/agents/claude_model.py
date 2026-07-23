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

"""Vogue Concierge — Claude on Agent Platform model adapter for Google ADK.

WHAT THIS FILE IS
-----------------
Google ADK (Agent Development Kit) talks to language models through a small
interface called ``BaseLlm``. ADK ships first-class support for Gemini, and a
LiteLLM bridge for "everything else". This file instead plugs **Claude models,
served on Google Cloud Vertex AI, into ADK directly via Anthropic's official
SDK** (``AnthropicVertex``). That gives us:

  * Native Anthropic tool-calling (no OpenAI-compatibility shim in the middle).
  * Authentication via Google Application Default Credentials (ADC) — the same
    credentials Agent Engine, Cloud Run, and your local ``gcloud`` already use.
    There is **no Anthropic API key** to manage; Vertex authorises the call.
  * One small, readable class you can lift into your own ADK project.

WHY A CUSTOM BaseLlm (AND NOT LiteLLM)
--------------------------------------
An earlier version of this project drove a different, less reliable model and
needed a pile of workarounds (forcing ``tool_choice``, one-tool-per-turn,
stripping leaked tool syntax, etc.) because that model called tools unreliably. Claude calls
tools natively and reliably, so the adapter below is deliberately small: convert
ADK's request shape to the Anthropic Messages API, send it, convert the reply
back. Nothing more.

HOW TO REUSE THIS IN YOUR OWN ENVIRONMENT
-----------------------------------------
1. Enable the Claude models you want in Vertex AI **Model Garden** for your
   project + region (one-time, in the Google Cloud console or via gcloud).
2. ``pip install "anthropic[vertex]"`` (the ``[vertex]`` extra pulls in the
   google-auth pieces).
3. Make sure ADC is available: ``gcloud auth application-default login`` locally,
   or rely on the service-account identity inside Agent Engine / Cloud Run.
4. Instantiate ``ClaudeVertexModel(model="claude-...")`` and pass it as the
   ``model=`` of an ADK ``Agent``.
"""

import json
import traceback
from typing import AsyncGenerator, Optional

from pydantic import ConfigDict

# ADK's model-plugin contract. ``generate_content_async`` is the one method we
# must implement; ADK calls it on every agent turn.
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

# ADK speaks google-genai content types internally (Content / Part / FunctionCall),
# so our adapter translates between those and Anthropic's block format.
from google.genai import types

from . import config


# ---------------------------------------------------------------------------
# Client cache
# ---------------------------------------------------------------------------
# AnthropicVertex clients are cheap but not free to build, and they are safe to
# reuse across requests. We cache one async client per Vertex region so that all
# four agents (which may share a region) reuse the same connection pool.
_CLIENTS: dict = {}


def _get_client(region: str):
    """Return a cached AsyncAnthropicVertex client for the given Vertex region.

    ``AsyncAnthropicVertex`` authenticates with Google ADC automatically — we
    only have to tell it which GCP project and region to target. No API key.
    """
    if region not in _CLIENTS:
        from anthropic import AsyncAnthropicVertex

        _CLIENTS[region] = AsyncAnthropicVertex(
            project_id=config.PROJECT_ID,
            region=region,
        )
    return _CLIENTS[region]


def _mark_cache(message: dict) -> None:
    """Add an ephemeral cache_control marker to a message's LAST content block so
    the conversation up to that point can be cached. Converts string content to
    block form when needed. All block types we produce (text / tool_use /
    tool_result) support cache_control, so this is always safe."""
    content = message.get("content")
    if isinstance(content, str):
        message["content"] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ]
    elif isinstance(content, list) and content and isinstance(content[-1], dict):
        content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}


class ClaudeVertexModel(BaseLlm):
    """An ADK model backed by a Claude model on Vertex AI.

    Each ADK ``Agent`` gets its own instance so we can pick *the right model for
    the job* — e.g. Opus for the Style Advisor's deep reasoning, Haiku for fast
    inventory lookups. See ``agents/config.py`` for the per-agent mapping.
    """

    # Pydantic v2 config: BaseLlm is a pydantic model, and we want to allow our
    # plain-Python helpers/fields without strict validation getting in the way.
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # --- Fields (overridable per agent) ------------------------------------
    # ``model`` is the bare Vertex model id, e.g. "claude-opus-4-8". Current
    # Claude models use the bare first-party id on Vertex; if your Model Garden
    # requires a dated snapshot, pass e.g. "claude-opus-4-8@<date>" instead.
    model: str = config.ORCHESTRATOR_MODEL
    # Per-response output cap. 4096 is plenty for a concierge turn and keeps
    # latency low. Raise it for long-form generation (and stream above ~16k).
    max_tokens: int = 4096
    # Vertex region that actually serves Claude (may differ from your Agent
    # Engine / BigQuery region — Claude availability is region-specific).
    region: str = config.CLAUDE_VERTEX_REGION
    # Optional reasoning effort: "low" | "medium" | "high" | "xhigh" | "max".
    # Left off (None) by default for predictable latency; the Style Advisor
    # turns it up for richer outfit reasoning. See config.py.
    effort: Optional[str] = None
    # Prompt caching: cache the stable prefix (tools + system) and the conversation
    # history so repeated calls within a turn's tool loop — and across turns —
    # reprocess far fewer input tokens. Safe: it only engages above a per-model
    # minimum prefix size and silently no-ops otherwise (never an error).
    cache_prompts: bool = True

    # ------------------------------------------------------------------ #
    #  ADK Schema  ->  JSON Schema (for Anthropic tool definitions)        #
    # ------------------------------------------------------------------ #
    def _schema_to_dict(self, schema) -> dict:
        """Recursively convert a google.genai ``Schema`` to a JSON-Schema dict.

        ADK describes each tool's parameters as a google-genai ``Schema``.
        Anthropic wants standard JSON Schema in the tool's ``input_schema``.
        This walks the schema and maps the enum-style genai types
        (STRING/INTEGER/...) to lowercase JSON-Schema types.
        """
        result: dict = {}

        if getattr(schema, "type", None):
            type_map = {
                "STRING": "string",
                "NUMBER": "number",
                "INTEGER": "integer",
                "BOOLEAN": "boolean",
                "ARRAY": "array",
                "OBJECT": "object",
            }
            type_str = str(schema.type).split(".")[-1].upper()
            result["type"] = type_map.get(type_str, type_str.lower())

        if getattr(schema, "description", None):
            result["description"] = schema.description

        if getattr(schema, "properties", None):
            result["properties"] = {
                name: self._schema_to_dict(prop)
                for name, prop in schema.properties.items()
            }

        if getattr(schema, "required", None):
            result["required"] = list(schema.required)

        if getattr(schema, "items", None):
            result["items"] = self._schema_to_dict(schema.items)

        if getattr(schema, "enum", None):
            result["enum"] = list(schema.enum)

        return result

    def _convert_tools(self, llm_request: LlmRequest) -> list:
        """ADK tool declarations -> Anthropic ``tools`` list.

        Anthropic tools are ``{name, description, input_schema}``. Every tool
        MUST have an object-typed ``input_schema`` (even if it has no params),
        so we normalise that below.
        """
        tools: list = []
        cfg = llm_request.config
        if not (cfg and cfg.tools):
            return tools

        for tool_group in cfg.tools:
            fds = getattr(tool_group, "function_declarations", None)
            if not fds:
                continue
            for fd in fds:
                # ADK can carry a tool's parameter schema in EITHER of two forms,
                # depending on the API variant:
                #   * fd.parameters            -> a google.genai Schema object
                #   * fd.parameters_json_schema -> a raw JSON-Schema dict
                # We must handle BOTH. In particular, ADK's AgentTool (how we wrap
                # specialists for delegation) uses the JSON-schema form for its
                # required `request` parameter. If we only read fd.parameters we'd
                # send Claude a no-parameter tool, and it would call the specialist
                # with empty args — so delegation would pass along no request and
                # the specialist would have nothing to act on.
                json_schema = getattr(fd, "parameters_json_schema", None)
                if json_schema:
                    schema = dict(json_schema)
                elif fd.parameters:
                    schema = self._schema_to_dict(fd.parameters)
                else:
                    schema = {"type": "object", "properties": {}}

                # Anthropic requires an object schema with a properties map.
                if schema.get("type") != "object":
                    schema = {"type": "object", "properties": {}}
                schema.setdefault("properties", {})

                tools.append(
                    {
                        "name": fd.name,
                        "description": fd.description or "",
                        "input_schema": schema,
                    }
                )
        return tools

    # ------------------------------------------------------------------ #
    #  ADK Content list  ->  Anthropic system + messages                  #
    # ------------------------------------------------------------------ #
    def _convert_contents(self, llm_request: LlmRequest):
        """Translate ADK's conversation into Anthropic's (system, messages).

        Two important format differences from ADK/Gemini:
          * Anthropic takes the system prompt as a separate ``system`` argument,
            NOT as the first message.
          * Tool calls and tool results are *content blocks* inside assistant /
            user messages (``tool_use`` / ``tool_result``), addressed to each
            other by a shared ``tool_use_id``.
        """
        system_text = ""
        cfg = llm_request.config
        if cfg and cfg.system_instruction:
            si = cfg.system_instruction
            if isinstance(si, str):
                system_text = si
            elif hasattr(si, "parts") and si.parts:
                system_text = " ".join(p.text for p in si.parts if p.text)

        messages: list = []
        for content in llm_request.contents:
            if not content.parts:
                continue

            role = content.role or "user"
            text_parts = [p for p in content.parts if p.text is not None]
            fc_parts = [p for p in content.parts if p.function_call is not None]
            fr_parts = [p for p in content.parts if p.function_response is not None]

            # (1) The model asked to call tools -> assistant message whose
            #     content carries one ``tool_use`` block per call.
            if fc_parts:
                blocks: list = []
                for p in text_parts:
                    if p.text:
                        blocks.append({"type": "text", "text": p.text})
                for p in fc_parts:
                    fc = p.function_call
                    blocks.append(
                        {
                            "type": "tool_use",
                            # The id ties this call to its result below. ADK
                            # preserves it; we synthesise a stable one if absent.
                            "id": fc.id or f"toolu_{fc.name}",
                            "name": fc.name,
                            "input": dict(fc.args) if fc.args else {},
                        }
                    )
                messages.append({"role": "assistant", "content": blocks})

            # (2) Tool results come back as a USER message of ``tool_result``
            #     blocks, each pointing at the matching ``tool_use_id``.
            elif fr_parts:
                blocks = []
                for p in fr_parts:
                    fr = p.function_response
                    tool_use_id = getattr(fr, "id", None) or f"toolu_{fr.name}"
                    data = (
                        fr.response
                        if isinstance(fr.response, dict)
                        else {"result": str(fr.response)}
                    )
                    blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            # Tool output is sent as a JSON string; ``default=str``
                            # keeps it safe if the dict holds non-JSON types.
                            "content": json.dumps(data, default=str),
                        }
                    )
                messages.append({"role": "user", "content": blocks})

            # (3) Plain text turn. ADK uses "model" for the assistant; Anthropic
            #     uses "assistant".
            elif text_parts:
                anth_role = "assistant" if role == "model" else "user"
                text = " ".join(p.text for p in text_parts if p.text)
                messages.append({"role": anth_role, "content": text})

        return system_text, messages

    # ------------------------------------------------------------------ #
    #  Anthropic response  ->  ADK LlmResponse                            #
    # ------------------------------------------------------------------ #
    def _parse_response(self, message) -> LlmResponse:
        """Convert an Anthropic ``Message`` back into an ADK ``LlmResponse``.

        Anthropic returns a list of content blocks. We map:
          * ``text``     -> a text Part
          * ``tool_use`` -> a FunctionCall Part (ADK then executes the tool and
                            loops back to us with the result)
          * ``thinking`` -> dropped (not surfaced to the user or replayed)
        """
        parts: list = []
        has_tool_call = False

        for block in message.content:
            if block.type == "text":
                parts.append(types.Part(text=block.text))
            elif block.type == "tool_use":
                has_tool_call = True
                parts.append(
                    types.Part(
                        function_call=types.FunctionCall(
                            id=block.id,
                            name=block.name,
                            args=dict(block.input) if block.input else {},
                        )
                    )
                )
            # Any other block type (e.g. thinking) is intentionally ignored.

        if not parts:
            parts.append(types.Part(text=""))

        return LlmResponse(
            content=types.Content(role="model", parts=parts),
            # The turn is "complete" when Claude answered with text rather than
            # requesting a tool. On a tool request, ADK runs the tool and calls
            # us again — so the turn is NOT complete yet.
            turn_complete=(message.stop_reason != "tool_use"),
        )

    # ------------------------------------------------------------------ #
    #  Main entry point — ADK calls this on every agent turn              #
    # ------------------------------------------------------------------ #
    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        """Send the converted request to Claude on Agent Platform and yield the reply.

        Flow per turn:
          1. ADK request  -> Anthropic (system, messages, tools).
          2. ``messages.create`` against the Vertex-hosted Claude model.
          3. Anthropic reply -> ADK LlmResponse (text and/or tool calls).
        ADK owns the agent loop (executing tools, re-invoking us); this method
        only handles a single request/response hop.
        """
        try:
            system_text, messages = self._convert_contents(llm_request)
            tools = self._convert_tools(llm_request)

            # Build the Anthropic request. Note what we DON'T set: no
            # ``tool_choice`` forcing, no manual ``thinking`` config — Claude
            # decides when to call tools on its own.
            kwargs: dict = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools
            if self.effort:
                # Effort tunes reasoning depth / token spend (Opus & Sonnet 4.6+).
                kwargs["output_config"] = {"effort": self.effort}

            if self.cache_prompts:
                # Cache the stable prefix (tools + system) via a marker on the
                # system block, and the conversation history via a marker on the
                # last message. Repeated calls reuse the cached prefix.
                if system_text:
                    kwargs["system"] = [
                        {"type": "text", "text": system_text,
                         "cache_control": {"type": "ephemeral"}}
                    ]
                if messages:
                    _mark_cache(messages[-1])
            elif system_text:
                kwargs["system"] = system_text

            client = _get_client(self.region)
            message = await client.messages.create(**kwargs)

            yield self._parse_response(message)

        except Exception as e:  # noqa: BLE001 - surface a friendly fallback
            traceback.print_exc()
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            text=(
                                "I'm having trouble reaching our style engine "
                                "right now. Please try again in a moment."
                            )
                        )
                    ],
                ),
                turn_complete=True,
                error_message=str(e),
            )
