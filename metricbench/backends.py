"""Model backends behind one small interface, so the same agent loop runs on any of them.

  claude  Claude through the Anthropic SDK (ANTHROPIC_API_KEY)
  openai  any OpenAI-compatible chat completions API: OpenAI, Gemini, Ollama, vLLM...
          (--base-url, and the key in METRICBENCH_API_KEY or OPENAI_API_KEY)
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx

# $ per million tokens: input, output, cache read. Claude API list prices (cache writes cost 1.25x input).
PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20), "claude-sonnet-5-5": (2.0, 10.0, 0.20),
          "claude-haiku-5-5": (0.10, 0.50, 0.01)}


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Turn:
    text: str
    calls: list[ToolCall]
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    stop: str = ""
    cost_usd: float = 0.0


@dataclass
class Conversation:
    system: str
    tools: list[dict]
    turns: list[Turn] = field(default_factory=list)

    def send(self, user_text: str) -> Turn:
        raise NotImplementedError

    def send_results(self, results: list[tuple[ToolCall, str, bool]]) -> Turn:
        raise NotImplementedError


class Backend:
    name = "base"
    model = ""

    def conversation(self, system: str, tools: list[dict]) -> Conversation:
        raise NotImplementedError


def load_dotenv(path: Path) -> None:
    """KEY=value lines from a local, git-ignored .env into the environment (existing values win)."""
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# ---------------------------------------------------------------------------------- Claude
class ClaudeConversation(Conversation):
    def __init__(self, system: str, tools: list[dict], client, model: str, effort: str):
        super().__init__(system, tools)
        self.client, self.model, self.effort = client, model, effort
        self.messages: list[dict] = []

    def _call(self) -> Turn:
        t0 = time.perf_counter()
        # Cache breakpoints: tools + system prompt (the same for every question), and the conversation so far.
        last = self.messages[-1]
        if isinstance(last["content"], str):
            last["content"] = [{"type": "text", "text": last["content"]}]
        for m in self.messages:
            if m["role"] == "user" and isinstance(m["content"], list):
                for block in m["content"]:
                    block.pop("cache_control", None)
        last["content"][-1]["cache_control"] = {"type": "ephemeral"}
        r = self.client.messages.create(
            model=self.model, max_tokens=16000,
            system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
            tools=[{**t, "strict": True} for t in self.tools], messages=self.messages,
            output_config={"effort": self.effort},
        )
        self.messages.append({"role": "assistant", "content": r.content})  # thinking blocks kept intact
        text = "".join(b.text for b in r.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in r.content if b.type == "tool_use"]
        u = r.usage
        read, write = u.cache_read_input_tokens or 0, u.cache_creation_input_tokens or 0
        p_in, p_out, p_read = PRICES.get(self.model, (0.0, 0.0, 0.0))
        cost = (u.input_tokens * p_in + write * p_in * 1.25 + read * p_read + u.output_tokens * p_out) / 1e6
        turn = Turn(text, calls, u.input_tokens + read + write, u.output_tokens, time.perf_counter() - t0,
                    r.stop_reason or "", cost)
        self.turns.append(turn)
        return turn

    def send(self, user_text: str) -> Turn:
        self.messages.append({"role": "user", "content": user_text})
        return self._call()

    def send_results(self, results):
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": c.id, "content": out, "is_error": err} for c, out, err in results]})
        return self._call()


class Claude(Backend):
    name = "claude"

    def __init__(self, model: str | None = None, effort: str = "medium", client=None):
        if client is None:
            try:
                import anthropic
            except ImportError as e:
                raise RuntimeError('the claude backend needs the Anthropic SDK: pip install "metricbench[claude]"') from e
            load_dotenv(Path.cwd() / ".env")
            client = anthropic.Anthropic(max_retries=4)
        self.client = client
        self.model = model or "claude-opus-5-5"
        self.effort = effort

    def conversation(self, system: str, tools: list[dict]) -> Conversation:
        return ClaudeConversation(system, tools, self.client, self.model, self.effort)


# ---------------------------------------------------------------------------------- OpenAI-compatible
class ChatConversation(Conversation):
    """The chat completions protocol most providers speak, Gemini and Ollama included."""

    def __init__(self, system: str, tools: list[dict], http: httpx.Client, model: str, price: tuple[float, float]):
        super().__init__(system, tools)
        self.http, self.model, self.price = http, model, price
        self.messages: list[dict] = [{"role": "system", "content": system}]
        self.functions = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                            "parameters": t["input_schema"]}} for t in tools]

    def _call(self) -> Turn:
        t0 = time.perf_counter()
        for attempt in range(6):
            r = self.http.post("chat/completions", json={"model": self.model, "messages": self.messages,
                                                         "tools": self.functions})
            if r.status_code not in (429, 500, 502, 503, 504):
                break
            time.sleep(min(2 ** attempt, 30))
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        choice = data["choices"][0]
        msg = choice["message"]
        self.messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls") and v is not None})
        calls = []
        for c in msg.get("tool_calls") or []:
            args = c["function"].get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError:
                    args = {}
            calls.append(ToolCall(c.get("id") or uuid.uuid4().hex[:12], c["function"]["name"], args))
        u = data.get("usage") or {}
        tin, tout = u.get("prompt_tokens", 0) or 0, u.get("completion_tokens", 0) or 0
        turn = Turn(msg.get("content") or "", calls, tin, tout, time.perf_counter() - t0,
                    choice.get("finish_reason") or "", (tin * self.price[0] + tout * self.price[1]) / 1e6)
        self.turns.append(turn)
        return turn

    def send(self, user_text: str) -> Turn:
        self.messages.append({"role": "user", "content": user_text})
        return self._call()

    def send_results(self, results):
        for c, out, _err in results:
            self.messages.append({"role": "tool", "tool_call_id": c.id, "name": c.name, "content": out})
        return self._call()


class OpenAICompatible(Backend):
    name = "openai"

    def __init__(self, model: str, base_url: str = "https://api.openai.com/v1/", api_key: str | None = None,
                 price: tuple[float, float] = (0.0, 0.0)):
        load_dotenv(Path.cwd() / ".env")
        key = api_key or os.environ.get("METRICBENCH_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self.http = httpx.Client(base_url=base_url.rstrip("/") + "/", headers=headers, timeout=600)
        self.model, self.price = model, price

    def conversation(self, system: str, tools: list[dict]) -> Conversation:
        return ChatConversation(system, tools, self.http, self.model, self.price)


def make(name: str, model: str | None = None, base_url: str | None = None,
         price: tuple[float, float] | None = None, effort: str = "medium") -> Backend:
    if name == "claude":
        return Claude(model, effort)
    if name == "openai":
        if not model:
            raise ValueError("the openai backend needs --model")
        return OpenAICompatible(model, base_url or "https://api.openai.com/v1/", price=price or (0.0, 0.0))
    raise ValueError(f"unknown backend {name} (claude, openai)")
