"""Local Hugging Face provider: a base model plus an optional LoRA adapter, run in-process with `transformers`.

For CPU rehearsals, offline tests and single-machine development. Not a serving path: the engine for production is
vLLM (ADR-009). Tool calling follows the Hermes / Qwen convention the chat template emits (`<tool_call>` JSON
blocks); structured output uses the prompt-level schema instruction and the caller's validation gate."""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from typing import Any

from protea.providers.base import ModelProvider, ProviderError
from protea.schemas.generation import GenerationRequest, GenerationResponse, ModelHealth, ToolCall, Usage

_TOOL_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
_TOOL_BLOCK = re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL)  # stripped from content even when malformed
MAX_NEW_TOKENS_CAP = 4096


def to_chat_messages(request: GenerationRequest) -> list[dict[str, Any]]:
    """The chat-template shape: tool calls on assistant turns, tool results as `tool` turns."""
    out: list[dict[str, Any]] = []
    for m in request.messages:
        if m.role == "assistant" and m.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": m.content or "",
                    "tool_calls": [
                        {"type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                        for c in m.tool_calls
                    ],
                }
            )
        elif m.role == "tool":
            out.append({"role": "tool", "name": m.name or "", "content": m.content or ""})
        else:
            out.append({"role": m.role, "content": m.content or ""})
    return out


def to_chat_tools(request: GenerationRequest) -> list[dict[str, Any]] | None:
    if not request.tools:
        return None
    return [
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
        for t in request.tools
    ]


def parse_tool_calls(text: str) -> tuple[str, list[ToolCall]]:
    """Split generated text into plain content and the tool calls it carries."""
    calls: list[ToolCall] = []
    for i, m in enumerate(_TOOL_CALL.finditer(text)):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("name"):
            args = obj.get("arguments") or obj.get("parameters") or {}
            calls.append(
                ToolCall(id=f"call_{i + 1}", name=str(obj["name"]), arguments=args if isinstance(args, dict) else {})
            )
    content = _TOOL_BLOCK.sub("", text).strip()
    return content, calls


class LocalHFProvider(ModelProvider):
    name = "local"
    supports_native_json_schema = False

    def __init__(
        self, model: str, adapter: str | None = None, *, device: str = "cpu", threads: int | None = None, **kw: Any
    ):
        super().__init__(model=model, **kw)
        self.adapter = adapter
        self.device = device
        self.threads = threads
        self._tok = None
        self._model = None
        self._lock = threading.Lock()

    # ---- loading ------------------------------------------------------------------------------
    def _load(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            try:
                import torch
                from transformers import AutoModelForCausalLM, AutoTokenizer
            except ImportError as exc:  # pragma: no cover - depends on the installed extras
                raise ProviderError(self.name, f"transformers/torch not installed: {exc}") from exc
            if self.threads:
                torch.set_num_threads(self.threads)
            tok = AutoTokenizer.from_pretrained(self.model)
            model = AutoModelForCausalLM.from_pretrained(self.model, dtype=torch.float32)
            if self.adapter:
                from peft import PeftModel

                model = PeftModel.from_pretrained(model, self.adapter)
                model = model.merge_and_unload()
            model.to(self.device)
            model.eval()
            if tok.pad_token_id is None:
                tok.pad_token = tok.eos_token
            self._tok, self._model = tok, model

    # ---- generation ----------------------------------------------------------------------------
    def _run(self, request: GenerationRequest) -> GenerationResponse:
        import torch

        self._load()
        tok, model = self._tok, self._model
        req = request if request.response_schema is None else self._with_schema_instruction(request)
        prompt = tok.apply_chat_template(
            to_chat_messages(req), tools=to_chat_tools(req), add_generation_prompt=True, tokenize=False
        )
        inputs = tok(prompt, return_tensors="pt").to(self.device)
        max_new = max(1, min(int(req.max_tokens), MAX_NEW_TOKENS_CAP))
        started = time.perf_counter()
        with torch.no_grad():
            out = model.generate(
                **inputs,
                max_new_tokens=max_new,
                do_sample=req.temperature > 0,
                temperature=req.temperature if req.temperature > 0 else None,
                pad_token_id=tok.pad_token_id,
            )
        new_tokens = out[0][inputs["input_ids"].shape[1] :]
        text = tok.decode(new_tokens, skip_special_tokens=True)
        content, calls = parse_tool_calls(text)
        finish = "tool_calls" if calls else ("length" if len(new_tokens) >= max_new else "stop")
        return GenerationResponse(
            content=content or None,
            tool_calls=calls,
            finish_reason=finish,  # type: ignore[arg-type]
            usage=Usage(input_tokens=int(inputs["input_ids"].shape[1]), output_tokens=int(len(new_tokens))),
            provider=self.name,
            model=self.model if not self.adapter else f"{self.model}+{self.adapter.rstrip('/').rsplit('/', 1)[-1]}",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def _generate(self, request: GenerationRequest) -> GenerationResponse:
        try:
            return await asyncio.to_thread(self._run, request)
        except ProviderError:
            raise
        except Exception as exc:  # model/runtime failures surface as provider errors, never as crashes
            raise ProviderError(self.name, f"{exc.__class__.__name__}: {str(exc)[:200]}") from exc

    async def health(self) -> ModelHealth:
        started = time.perf_counter()
        try:
            await asyncio.to_thread(self._load)
        except ProviderError as exc:
            return ModelHealth(provider=self.name, model=self.model, ok=False, detail=str(exc))
        return ModelHealth(
            provider=self.name, model=self.model, ok=True, latency_ms=int((time.perf_counter() - started) * 1000)
        )
