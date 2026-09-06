"""Reference provider: answers every task with its own `reference`. A reference run must score 100% on referenced
tasks — it proves the evaluators are satisfiable and the references are right, not that any model is good."""

from __future__ import annotations

from protea.evaluation.tasks import EvalTask
from protea.providers.base import ModelProvider
from protea.schemas.generation import GenerationRequest, GenerationResponse, Usage


class ReferenceProvider(ModelProvider):
    name = "reference"
    supports_native_json_schema = True

    def __init__(self, tasks: list[EvalTask], **kw: object):
        super().__init__(model="reference", **kw)  # type: ignore[arg-type]
        self._tasks = {t.id: t for t in tasks}
        self._calls: dict[str, int] = {}

    async def _generate(self, request: GenerationRequest) -> GenerationResponse:
        task = self._tasks.get(request.metadata.agent_id or "")
        usage = Usage(input_tokens=sum(len(m.content or "") for m in request.messages) // 4, output_tokens=16)
        if task is None or task.reference is None:
            return GenerationResponse(content="", usage=usage, provider=self.name, model=self.model)
        n = self._calls.get(task.id, 0)
        self._calls[task.id] = n + 1
        ref = task.reference
        if ref.tool_calls and n == 0:
            return GenerationResponse(
                tool_calls=ref.tool_calls, finish_reason="tool_calls", usage=usage, provider=self.name, model=self.model
            )
        return GenerationResponse(content=ref.text, usage=usage, provider=self.name, model=self.model)
