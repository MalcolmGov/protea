import json

import httpx

from protea.providers.google import GoogleProvider
from protea.schemas.generation import GenerationRequest, Message, ToolSchema


async def test_function_call_and_schema_payload():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "modelVersion": "gemini-2.5-flash",
                "candidates": [
                    {
                        "content": {
                            "parts": [{"functionCall": {"name": "get_order_status", "args": {"order_id": "4821"}}}]
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 3},
            },
        )

    p = GoogleProvider(
        model="gemini-2.5-flash", api_key="k", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    tool = ToolSchema(
        name="get_order_status",
        parameters={"type": "object", "properties": {"order_id": {"type": "string"}}, "additionalProperties": False},
    )
    req = GenerationRequest(
        messages=[Message(role="system", content="s"), Message(role="user", content="u")],
        tools=[tool],
        response_schema={"type": "object", "title": "X", "properties": {"a": {"type": "string"}}},
    )
    resp = await p.generate(req)
    assert resp.finish_reason == "tool_calls" and resp.tool_calls[0].arguments == {"order_id": "4821"}
    assert seen["url"].endswith("/models/gemini-2.5-flash:generateContent") and seen["key"] == "k"
    body = seen["body"]
    assert body["systemInstruction"]["parts"][0]["text"] == "s"
    assert "additionalProperties" not in json.dumps(body["tools"])
    assert "title" not in body["generationConfig"]["responseSchema"]
    assert body["generationConfig"]["responseMimeType"] == "application/json"
