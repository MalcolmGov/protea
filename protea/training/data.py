"""ADR-003 JSONL → conversational records for the trainer, with the golden guard and the dataset pin (spec §25, §16).

Tool calls are rendered in the Hermes/Qwen text format inside assistant content so one rendering works for every
base model's chat template; tool results keep role "tool".
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from protea.schemas.examples import Split, TrainingExample, iter_examples
from protea.schemas.generation import Message, ToolSchema

TOOLS_PREAMBLE = (
    "\n\n# Tools\n\nYou may call one or more functions to assist with the user query.\n"
    "You are provided with function signatures within <tools></tools> XML tags:\n<tools>\n{tools}\n</tools>\n\n"
    "For each function call, return a json object with function name and arguments within "
    '<tool_call></tool_call> XML tags:\n<tool_call>\n{{"name": <function-name>, "arguments": <args-json-object>}}\n</tool_call>'
)


class DatasetStats(BaseModel):
    path: str
    sha256: str
    examples: int
    approx_tokens: int
    by_task_type: dict[str, int] = Field(default_factory=dict)
    by_language: dict[str, int] = Field(default_factory=dict)
    synthetic: int = 0
    max_chars: int = 0


class GoldenLeakError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tools_text(tools: list[ToolSchema]) -> str:
    return "\n".join(
        json.dumps({"type": "function", "function": t.model_dump(exclude={"strict"})}, ensure_ascii=False)
        for t in tools
    )


def render_message(m: Message) -> dict[str, str]:
    content = m.content or ""
    if m.role == "assistant" and m.tool_calls:
        calls = "".join(
            "\n<tool_call>\n"
            + json.dumps({"name": c.name, "arguments": c.arguments}, ensure_ascii=False)
            + "\n</tool_call>"
            for c in m.tool_calls
        )
        content = (content + calls).lstrip("\n")
    return {"role": m.role, "content": content}


def render_example(ex: TrainingExample) -> dict[str, Any]:
    """{"messages": [{role, content}...]} with tool schemas folded into the system turn."""
    msgs = [render_message(m) for m in ex.messages]
    if ex.tools:
        preamble = TOOLS_PREAMBLE.format(tools=_tools_text(ex.tools))
        if msgs[0]["role"] == "system":
            msgs[0] = {"role": "system", "content": msgs[0]["content"] + preamble}
        else:
            msgs.insert(0, {"role": "system", "content": "You are a helpful assistant." + preamble})
    return {"messages": msgs}


def render_chatml(record: dict[str, Any]) -> str:
    """Plain-text rendering used for token estimates and for tokenizers without a chat template."""
    return "".join(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in record["messages"])


def load_records(path: Path, *, max_examples: int | None = None) -> tuple[list[dict[str, Any]], DatasetStats]:
    """Load and render a JSONL split. Refuses any example marked golden (spec §25) or that fails validation."""
    records: list[dict[str, Any]] = []
    by_task: dict[str, int] = {}
    by_lang: dict[str, int] = {}
    tokens = synthetic = max_chars = 0
    for n, line in iter_examples(path):
        ex = TrainingExample.model_validate_json(line)
        if ex.metadata.split == Split.GOLDEN:
            raise GoldenLeakError(f"{path}:{n}: golden example {ex.metadata.id} in a training input")
        rec = render_example(ex)
        records.append(rec)
        tokens += ex.approx_tokens()
        synthetic += ex.metadata.synthetic
        by_task[ex.metadata.task_type.value] = by_task.get(ex.metadata.task_type.value, 0) + 1
        by_lang[ex.metadata.language] = by_lang.get(ex.metadata.language, 0) + 1
        max_chars = max(max_chars, len(render_chatml(rec)))
        if max_examples and len(records) >= max_examples:
            break
    stats = DatasetStats(
        path=str(path),
        sha256=sha256_file(path),
        examples=len(records),
        approx_tokens=tokens,
        by_task_type=dict(sorted(by_task.items())),
        by_language=dict(sorted(by_lang.items())),
        synthetic=synthetic,
        max_chars=max_chars,
    )
    return records, stats


def check_dataset_pin(dataset_key: str, train_stats: DatasetStats, registry_entries: list[Any]) -> list[str]:
    """The registry is the source of truth: the training file must be the registered file, byte for byte."""
    entry = next((e for e in registry_entries if e.key == dataset_key), None)
    if entry is None:
        return [f"dataset {dataset_key!r} is not registered (run `protea dataset build` or pass --allow-unregistered)"]
    if entry.sha256 != train_stats.sha256:
        return [f"train file sha256 {train_stats.sha256[:12]} != registered {entry.sha256[:12]} for {dataset_key}"]
    if entry.golden:
        return [f"dataset {dataset_key} is a golden set and can never be trained on"]
    return []
