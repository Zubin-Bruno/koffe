"""
Chat endpoint — streams LLM responses via SSE and executes coffee searches
using tool calling through OpenRouter (OpenAI-compatible API).
"""

import json
import os

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from openai import AsyncOpenAI
from pydantic import BaseModel

from koffe.api.routes.coffees import _apply_filters, _coffee_to_dict
from koffe.db.database import SessionLocal
from koffe.db.models import Coffee

router = APIRouter()

# ── OpenAI client pointed at OpenRouter ──────────────────────────────
# The openai library is just a convenient HTTP client.  We override
# base_url so every request goes to OpenRouter, never to OpenAI.
client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

CHAT_MODEL = os.getenv("CHAT_MODEL", "google/gemini-2.0-flash-001")

# ── System prompt (barista personality) ──────────────────────────────
SYSTEM_PROMPT = """\
You are a friendly barista assistant for Koffe, an Argentine specialty coffee catalog.
Your job is to help users discover coffees by understanding their preferences.

Rules:
- Match the user's language (Spanish → Spanish, English → English)
- Use the search_coffees tool when the user describes preferences or asks for recommendations
- After searching, summarize results conversationally — mention coffee names, roasters, and key attributes
- If no matches, suggest relaxing filters
- Keep responses concise (2-4 sentences)
- Intensity scales (acidity, sweetness, body) go from 1 to 5
- Be warm and enthusiastic about coffee
- Do NOT use markdown formatting — no asterisks, headers, or bold. Plain text only.
"""

# ── Tool definition ──────────────────────────────────────────────────
# This JSON schema tells the LLM what parameters it can pass when it
# decides to search.  It mirrors our existing filter system.
SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_coffees",
        "description": (
            "Search the coffee catalog with optional filters. "
            "Returns matching coffees from Argentine specialty roasters."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Free text search across name, description, attributes",
                },
                "origin": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Origin countries, e.g. ['Colombia', 'Ethiopia']",
                },
                "process": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Processing methods, e.g. ['Natural', 'Washed']",
                },
                "variety": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Coffee varieties, e.g. ['Gesha', 'Bourbon']",
                },
                "brew_method": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Brew methods, e.g. ['Espresso', 'Filtro']",
                },
                "tasting_note": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tasting notes, e.g. ['Chocolate', 'Frutal']",
                },
                "acidity_min": {"type": "integer", "minimum": 1, "maximum": 5},
                "acidity_max": {"type": "integer", "minimum": 1, "maximum": 5},
                "sweetness_min": {"type": "integer", "minimum": 1, "maximum": 5},
                "sweetness_max": {"type": "integer", "minimum": 1, "maximum": 5},
                "body_min": {"type": "integer", "minimum": 1, "maximum": 5},
                "body_max": {"type": "integer", "minimum": 1, "maximum": 5},
                "min_price": {
                    "type": "integer",
                    "description": "Minimum price in ARS (not cents)",
                },
                "max_price": {
                    "type": "integer",
                    "description": "Maximum price in ARS (not cents)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 12)",
                    "default": 12,
                },
            },
            "required": [],
        },
    },
}


# ── Request / response models ────────────────────────────────────────
class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


# ── Helpers ──────────────────────────────────────────────────────────
def _execute_search(args: dict, db) -> list[dict]:
    """Run a coffee search reusing the existing filter logic from coffees.py."""
    q = db.query(Coffee).filter(Coffee.is_available == True)

    q = _apply_filters(
        q,
        origin=args.get("origin") or [],
        process=args.get("process") or [],
        roaster_id_int=[],
        acidity_min_int=args.get("acidity_min"),
        acidity_max_int=args.get("acidity_max"),
        sweetness_min_int=args.get("sweetness_min"),
        sweetness_max_int=args.get("sweetness_max"),
        body_min_int=args.get("body_min"),
        body_max_int=args.get("body_max"),
        variety=args.get("variety") or [],
        brew_method=args.get("brew_method") or [],
        search=args.get("search"),
        tasting_notes=args.get("tasting_note") or [],
    )

    min_price = args.get("min_price")
    max_price = args.get("max_price")
    if min_price is not None:
        q = q.filter(Coffee.price_cents >= min_price * 100)
    if max_price is not None:
        q = q.filter(Coffee.price_cents <= max_price * 100)

    limit = args.get("limit", 12)
    coffees = q.order_by(Coffee.name).limit(limit).all()
    return [_coffee_to_dict(c) for c in coffees]


def _summarise_for_llm(results: list[dict]) -> str:
    """Build a concise text summary of search results for the second LLM call."""
    if not results:
        return "No coffees found matching those filters."
    lines = []
    for c in results:
        parts = [c["name"]]
        if c.get("roaster_name"):
            parts.append(f"by {c['roaster_name']}")
        if c.get("origin_country"):
            parts.append(f"from {c['origin_country']}")
        if c.get("process"):
            parts.append(f"({c['process']})")
        if c.get("price_display"):
            parts.append(f"- {c['price_display']}")
        lines.append(", ".join(parts))
    return f"Found {len(results)} coffees:\n" + "\n".join(lines)


def _sse(data: dict) -> str:
    """Format a dict as a single SSE data line."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── Main endpoint ────────────────────────────────────────────────────
@router.post("/api/chat")
async def chat_endpoint(request: Request, chat_req: ChatRequest):
    """
    Streaming chat endpoint.  The frontend sends the full conversation
    history; we prepend a system prompt, call the LLM, and stream back
    SSE events (text tokens, search results as HTML, filter chips).
    """

    async def event_stream():
        # We open our own DB session because SSE generators outlive the
        # normal FastAPI request lifecycle (Depends would close too early).
        db = SessionLocal()
        try:
            # ── 1. Build messages array with system prompt ────────────
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            for msg in chat_req.messages:
                messages.append({"role": msg.role, "content": msg.content})

            # ── 2. First LLM call (streaming, with tool definition) ──
            stream = await client.chat.completions.create(
                model=CHAT_MODEL,
                messages=messages,
                tools=[SEARCH_TOOL],
                stream=True,
            )

            full_text = ""
            tool_calls = {}  # index → {id, name, arguments}

            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                # Stream text tokens to the frontend immediately
                if delta.content:
                    full_text += delta.content
                    yield _sse({"type": "text", "content": delta.content})

                # Accumulate tool-call fragments (they arrive in pieces)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls:
                            tool_calls[idx] = {
                                "id": "", "name": "", "arguments": "",
                            }
                        if tc.id:
                            tool_calls[idx]["id"] = tc.id
                        if tc.function and tc.function.name:
                            tool_calls[idx]["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            tool_calls[idx]["arguments"] += tc.function.arguments

            # ── 3. Execute tool calls (if any) ───────────────────────
            if tool_calls:
                for idx in sorted(tool_calls):
                    tc = tool_calls[idx]
                    if tc["name"] != "search_coffees":
                        continue

                    yield _sse({"type": "status", "content": "searching"})

                    try:
                        args = json.loads(tc["arguments"])
                    except json.JSONDecodeError:
                        args = {}

                    # Run the DB query
                    results = _execute_search(args, db)

                    # Send filter chips so the UI shows what was searched
                    yield _sse({"type": "filters", "args": args})

                    # Render coffee cards to HTML using the same Jinja2
                    # template the rest of the site uses
                    templates = request.app.state.templates
                    html = templates.env.get_template(
                        "coffee_cards.html"
                    ).render(
                        coffees=results,
                        total=len(results),
                        has_filters=True,
                        show_all=False,
                    )
                    yield _sse({"type": "cards_html", "html": html})

                    # ── 4. Second LLM call so it can discuss results ─
                    messages_with_tool = messages + [
                        {
                            "role": "assistant",
                            "content": full_text or None,
                            "tool_calls": [
                                {
                                    "id": tc["id"],
                                    "type": "function",
                                    "function": {
                                        "name": tc["name"],
                                        "arguments": tc["arguments"],
                                    },
                                }
                            ],
                        },
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": _summarise_for_llm(results),
                        },
                    ]

                    stream2 = await client.chat.completions.create(
                        model=CHAT_MODEL,
                        messages=messages_with_tool,
                        stream=True,
                    )

                    async for chunk in stream2:
                        if not chunk.choices:
                            continue
                        delta = chunk.choices[0].delta
                        if delta.content:
                            full_text += delta.content
                            yield _sse({
                                "type": "text", "content": delta.content,
                            })

            # ── 5. Signal that the stream is done ────────────────────
            yield _sse({"type": "done", "full_text": full_text})

        except Exception as e:
            logger.error(f"Chat error: {e}")
            yield _sse({"type": "error", "content": str(e)})
        finally:
            db.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
