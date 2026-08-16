"""OpenInference tracing for DeepSeek Harness turns only."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from deepseek_harness import DeepSeekHarness, Notification, RunResult
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from phoenix.otel import OpenInferenceMimeTypeValues, OpenInferenceSpanKindValues, SpanAttributes, register

TRACER = trace.get_tracer("deepseek-harness")


def configure():
    return register(
        project_name=os.environ.get("PHOENIX_PROJECT", "deepseek-harness-poc"),
        endpoint=os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006/v1/traces"),
        protocol="http/protobuf",
        batch=True,
    )


def run_agent(
    harness: DeepSeekHarness,
    prompt: str,
    runtime_session_id: str,
    session_id: str,
    on_notification: Callable[[Notification], None] | None = None,
) -> RunResult:
    capture_content = os.environ.get("PHOENIX_CAPTURE_CONTENT", "true").lower() not in {"0", "false", "no"}
    attributes: dict[str, Any] = {
        SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.AGENT.value,
        SpanAttributes.SESSION_ID: session_id,
    }
    if capture_content:
        attributes.update(
            {
                SpanAttributes.INPUT_VALUE: prompt,
                SpanAttributes.INPUT_MIME_TYPE: OpenInferenceMimeTypeValues.TEXT.value,
            }
        )

    with TRACER.start_as_current_span("DeepSeek Harness", attributes=attributes) as span:
        result = harness.run(prompt, session_id=runtime_session_id, on_notification=on_notification)
        _record_children(result.events, prompt, session_id, capture_content)
        span.set_attribute("agent.finish_reason", result.finish_reason or "unknown")
        if capture_content:
            span.set_attribute(SpanAttributes.OUTPUT_VALUE, result.final_response)
            span.set_attribute(SpanAttributes.OUTPUT_MIME_TYPE, OpenInferenceMimeTypeValues.TEXT.value)
        if result.finish_reason == "error" or not result.final_response:
            span.set_status(Status(StatusCode.ERROR, "Agent turn failed"))
        else:
            span.set_status(Status(StatusCode.OK))
        return result


def _record_children(events: list[dict[str, Any]], prompt: str, session_id: str, capture_content: bool) -> None:
    step_starts: dict[tuple[Any, Any], int] = {}
    tool_calls: dict[str, dict[str, Any]] = {}
    llm_inputs = [prompt]
    config: dict[str, Any] = {}

    for event in events:
        kind = event.get("type")
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        if kind == "request/header":
            header = data.get("header")
            if isinstance(header, dict) and isinstance(header.get("config"), dict):
                config = header["config"]
        elif kind == "step/start":
            step_starts[(data.get("turn"), data.get("step"))] = _time(event)
        elif kind == "assistant/message":
            _record_llm(event, data, step_starts, llm_inputs, config, session_id, capture_content)
            llm_inputs.clear()
        elif kind == "tool/call" and isinstance(data.get("callId"), str):
            tool_calls[data["callId"]] = {"event": event, "data": data}
        elif kind == "tool/result":
            output, is_error, call_id = _tool_output(data)
            call = tool_calls.get(call_id)
            if call is not None:
                _record_tool(call, event, output, is_error, session_id, capture_content)
            if output:
                llm_inputs.append(output)


def _record_llm(
    event: dict[str, Any],
    data: dict[str, Any],
    step_starts: dict[tuple[Any, Any], int],
    inputs: list[str],
    config: dict[str, Any],
    session_id: str,
    capture_content: bool,
) -> None:
    message = data.get("message")
    if not isinstance(message, dict):
        return
    source = message.get("source") if isinstance(message.get("source"), dict) else {}
    provider = str(source.get("provider") or config.get("provider") or "unknown")
    model = str(source.get("model") or config.get("model") or "unknown")
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    attributes: dict[str, Any] = {
        SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.LLM.value,
        SpanAttributes.SESSION_ID: session_id,
        SpanAttributes.LLM_SYSTEM: provider,
        SpanAttributes.LLM_MODEL_NAME: model,
        SpanAttributes.LLM_INVOCATION_PARAMETERS: json.dumps(config),
    }
    input_tokens = usage.get("inputTokens")
    output_tokens = usage.get("outputTokens")
    if isinstance(input_tokens, int):
        attributes[SpanAttributes.LLM_TOKEN_COUNT_PROMPT] = input_tokens
    if isinstance(output_tokens, int):
        attributes[SpanAttributes.LLM_TOKEN_COUNT_COMPLETION] = output_tokens
    if isinstance(input_tokens, int) and isinstance(output_tokens, int):
        attributes[SpanAttributes.LLM_TOKEN_COUNT_TOTAL] = input_tokens + output_tokens
    if capture_content:
        if inputs:
            attributes[SpanAttributes.INPUT_VALUE] = inputs[0] if len(inputs) == 1 else json.dumps(inputs)
            attributes[SpanAttributes.INPUT_MIME_TYPE] = OpenInferenceMimeTypeValues.TEXT.value
        attributes[SpanAttributes.OUTPUT_VALUE] = json.dumps(message.get("content") or [])
        attributes[SpanAttributes.OUTPUT_MIME_TYPE] = OpenInferenceMimeTypeValues.JSON.value

    span = TRACER.start_span(
        f"{provider} chat",
        attributes=attributes,
        start_time=step_starts.get((data.get("turn"), data.get("step"))),
    )
    span.set_status(Status(StatusCode.OK))
    span.end(end_time=_time(event))


def _record_tool(
    call: dict[str, Any],
    result_event: dict[str, Any],
    output: str,
    is_error: bool,
    session_id: str,
    capture_content: bool,
) -> None:
    data = call["data"]
    name = str(data.get("name") or "tool")
    attributes: dict[str, Any] = {
        SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.TOOL.value,
        SpanAttributes.SESSION_ID: session_id,
        SpanAttributes.TOOL_NAME: name,
        "tool.call.id": str(data.get("callId")),
    }
    if capture_content:
        attributes[SpanAttributes.INPUT_VALUE] = str(data.get("arguments") or "")
        attributes[SpanAttributes.INPUT_MIME_TYPE] = OpenInferenceMimeTypeValues.JSON.value
        attributes[SpanAttributes.OUTPUT_VALUE] = output
        attributes[SpanAttributes.OUTPUT_MIME_TYPE] = OpenInferenceMimeTypeValues.JSON.value

    span = TRACER.start_span(name, attributes=attributes, start_time=_time(call["event"]))
    span.set_status(Status(StatusCode.ERROR if is_error else StatusCode.OK))
    span.end(end_time=_time(result_event))


def _tool_output(data: dict[str, Any]) -> tuple[str, bool, str]:
    message = data.get("message")
    if not isinstance(message, dict):
        return "", True, ""
    source = message.get("source") if isinstance(message.get("source"), dict) else {}
    outputs: list[str] = []
    is_error = False
    for block in message.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool-result":
            continue
        is_error = is_error or block.get("isError") is True
        for part in block.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "text":
                outputs.append(str(part.get("text") or ""))
    return "".join(outputs), is_error, str(source.get("callId") or "")


def _time(event: dict[str, Any]) -> int:
    value = event.get("time")
    return int(value * 1_000_000) if isinstance(value, (int, float)) else 0
