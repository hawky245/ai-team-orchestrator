"""Ad-hoc verification of generate_with_tools — stubs the OpenAI client."""
import asyncio
import json
import os
import sys

os.environ.setdefault("LLM_API_KEY", "dummy-key-for-constructor")

from src.providers.base_provider import NvidiaNimProvider, ProviderConfig, ProviderError
from src.tools.worker_tools import WORKER_TOOLS, TOOL_REGISTRY


def make_msg(content=None, tool_calls=None):
    class F: pass
    class M: pass
    m = M()
    m.content = content
    m.tool_calls = tool_calls
    m.role = "assistant"
    if tool_calls:
        m.function_calls = None
    return m


def make_tool_call(id_, name, arguments):
    class Fn: pass
    class Tc: pass
    fn = Fn(); fn.name = name; fn.arguments = arguments
    tc = Tc(); tc.id = id_; tc.function = fn; tc.type = "function"
    return tc


def make_response(msg, finish_reason="stop"):
    class R: pass
    r = R()
    r.choices = [type("C", (), {"message": msg, "finish_reason": finish_reason})()]
    r.usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 5})()
    return r


class FakeClient:
    """Scripted chat.completions responses; records every request."""
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.script.pop(0)


def run_case(label, script, expect_error=False):
    cfg = ProviderConfig(model_id="test-model", temperature=0.2, max_tokens=1024)
    provider = NvidiaNimProvider(cfg)
    client = FakeClient(script)
    provider.client = client
    events = []

    async def on_tool_call(name, args):
        events.append(("tool_execution", name, args))

    async def go():
        return await provider.generate_with_tools(
            "sys", "user", WORKER_TOOLS, TOOL_REGISTRY, on_tool_call
        )

    try:
        result = asyncio.run(go())
        assert expect_error is False, f"{label}: expected error but got success"
        return label, client, events, result
    except ProviderError as e:
        if not expect_error:
            raise AssertionError(f"{label}: unexpected ProviderError {e}")
        print(f"[{label}] ProviderError (expected): {e}")


# Case 1: model calls web_search, then synthesizes final content
tool_call_msg = make_msg(content=None, tool_calls=[make_tool_call("call_1", "web_search", json.dumps({"query": "lighthouses"}))])
final_msg = make_msg(content='{"raw_output": "The answer", "artifacts": []}')
label, client, events, result = run_case("happy-path", [tool_call_msg and make_response(tool_call_msg, "tool_calls"), make_response(final_msg)])

req1, req2 = client.requests
assert "tools" in req1 and req1["tool_choice"] == "auto", "first call must advertise tools"
assert len(req2["messages"]) == 4, f"second call must carry history, got {len(req2['messages'])} msgs"
assistant_msg = req2["messages"][2]
assert assistant_msg["role"] == "assistant" and assistant_msg["tool_calls"][0]["id"] == "call_1"
tool_msg = req2["messages"][3]
assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call_1"
mock_payload = json.loads(tool_msg["content"])
assert mock_payload["query"] == "lighthouses", "tool result must come from web_search(query)"
assert events == [("tool_execution", "web_search", {"query": "lighthouses"})], f"event mismatch: {events}"
assert result.content == '{"raw_output": "The answer", "artifacts": []}'
print("[happy-path] PASS: tools advertised -> intercepted -> executed -> tool result appended -> 2nd call -> final content")
print("[happy-path] tool_execution broadcast:", events[0])

# Case 2: hallucinated tool name -> error returned to model, never executed; loop continues
bad_msg = make_msg(content=None, tool_calls=[make_tool_call("call_x", "launch_missiles", "{}")])
label, client, events, result = run_case("hallucinated-tool", [make_response(bad_msg, "tool_calls"), make_response(final_msg)])
tool_msg = client.requests[1]["messages"][3]
assert tool_msg["tool_call_id"] == "call_x"
assert "unknown tool" in tool_msg["content"], tool_msg["content"]
assert events == [], "hallucinated tool must NOT broadcast a tool_execution event"
print("[hallucinated-tool] PASS: rejected with error result, no broadcast, loop recovered:", tool_msg["content"][:80])

# Case 3: malformed arguments JSON -> rejected, no crash
bad_args = make_msg(content=None, tool_calls=[make_tool_call("call_b", "read_file", "{not json")])
label, client, events, result = run_case("bad-args", [make_response(bad_args, "tool_calls"), make_response(final_msg)])
tool_msg = client.requests[1]["messages"][3]
assert "could not parse arguments" in tool_msg["content"]
assert events == []
print("[bad-args] PASS: malformed arguments rejected:", tool_msg["content"][:80])

# Case 4: wrong kwargs for a real tool -> TypeError caught as tool error
wrong_kwargs = make_msg(content=None, tool_calls=[make_tool_call("call_w", "web_search", json.dumps({"q": "typo arg"}))])
label, client, events, result = run_case("bad-kwargs", [make_response(wrong_kwargs, "tool_calls"), make_response(final_msg)])
tool_msg = client.requests[1]["messages"][3]
assert "invalid arguments" in tool_msg["content"], tool_msg["content"]
print("[bad-kwargs] PASS: TypeError surfaced as tool error:", tool_msg["content"][:90])

# Case 5: no tool call at all -> single request, straight answer
label, client, events, result = run_case("no-tools", [make_response(final_msg)])
assert len(client.requests) == 1 and events == []
print("[no-tools] PASS: direct answer in one call")

print("\nALL 5 CASES PASS")
