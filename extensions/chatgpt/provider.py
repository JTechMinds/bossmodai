"""Responses transport owned by the ChatGPT extension.

Both sync System AI and async agent calls use the same input conversion,
option validation, event accumulator and error mapping. Clients are injected.
"""

from __future__ import annotations
import asyncio
import json
import time
from openai import AsyncOpenAI, OpenAI, OpenAIError
from core.extensions.model_provider import (
    ProviderCompletion,
    ProviderRequest,
    ProviderTimeout,
)
from .config import ChatGPTConfig, ChatGPTError, ChatGPTUsageLimitError

_USAGE_LIMIT_CODE = "subscription_sharing_usage_limit_exceeded"
_ERRORS = {
    _USAGE_LIMIT_CODE: "Your ChatGPT usage limit was reached. Check ChatGPT Settings → Usage or wait for it to reset.",
    "subscription_sharing_usage_unavailable": "ChatGPT plan usage is unavailable for this account or workspace. Check ChatGPT Settings.",
}


def _failure(code=None):
    # OpenAI documents the usage-limit stop as "pause, don't retry"; only that
    # code gets the typed error the host refuses to retry.
    if code == _USAGE_LIMIT_CODE:
        return ChatGPTUsageLimitError(_ERRORS[code])
    return ChatGPTError(
        _ERRORS.get(
            code,
            "ChatGPT couldn’t complete this request. Check your connection or sign in again in Extensions.",
        )
    )


def response_input(messages: list[dict]) -> list[dict]:
    """Translate text/image chat history without sending unsupported system items."""
    result = []
    for message in messages:
        role = message.get("role", "user")
        if role == "system":
            role = "developer"
        if role not in {"developer", "user", "assistant"}:
            raise ChatGPTError(
                f"ChatGPT connection cannot accept a {role} history message."
            )
        content = message.get("content", "")
        if isinstance(content, str):
            result.append({"role": role, "content": content})
            continue
        else:
            parts = []
            for part in content:
                if part.get("type") == "text":
                    parts.append(
                        {
                            "type": (
                                "output_text" if role == "assistant" else "input_text"
                            ),
                            "text": part["text"],
                        }
                    )
                elif part.get("type") == "image_url" and role != "assistant":
                    image = part["image_url"]
                    parts.append(
                        {
                            "type": "input_image",
                            "image_url": image["url"],
                            "detail": image.get("detail", "auto"),
                        }
                    )
                else:
                    raise ChatGPTError(
                        "This attachment type is not supported by the ChatGPT connection."
                    )
        if role == "assistant":
            result.append(
                {"role": role, "content": "".join(part["text"] for part in parts)}
            )
        else:
            result.append({"role": role, "content": parts})
    return result


def request_body(request):
    options = {}
    if request.extra_body:
        try:
            options = json.loads(request.extra_body)
        except (ValueError, TypeError):
            raise ChatGPTError(
                "ChatGPT connection options must be valid JSON."
            ) from None
        if not isinstance(options, dict) or set(options) - {"reasoning", "text"}:
            raise ChatGPTError(
                "ChatGPT connection options support only reasoning and text settings."
            )
    return {
        "model": request.model,
        "input": response_input(request.messages),
        "store": False,
        "stream": True,
        **options,
    }


class ResponseAccumulator:
    def __init__(self):
        self.completed = None
        self.text_parts = {}

    def accept(self, event):
        if event.type in {"response.output_text.delta", "response.output_text.done"}:
            key = (
                getattr(event, "output_index", 0),
                getattr(event, "content_index", 0),
            )
            if event.type == "response.output_text.delta":
                self.text_parts.setdefault(key, []).append(event.delta)
            else:
                # The done event replaces its deltas; it must not duplicate them.
                self.text_parts[key] = [event.text]
        elif event.type == "response.completed":
            self.completed = event.response
        elif event.type in {"response.failed", "response.incomplete", "error"}:
            error = getattr(getattr(event, "response", None), "error", None)
            raise _failure(getattr(error, "code", None) or getattr(event, "code", None))

    def require_completed(self):
        """A completed request proves model access, even without reply text."""
        if self.completed is None:
            raise ChatGPTError(
                "ChatGPT’s response was interrupted before completion. Try the task again."
            )
        return self.completed

    def finish(self):
        response = self.require_completed()
        parts = {key: "".join(chunks) for key, chunks in self.text_parts.items()}
        for output_index, item in enumerate(response.output or []):
            if item.type != "message":
                continue
            for content_index, part in enumerate(item.content):
                if part.type == "output_text":
                    key = (output_index, content_index)
                    if part.text.strip() or key not in parts:
                        parts[key] = part.text
        text = "".join(parts[key] for key in sorted(parts))
        if not text.strip():
            raise ChatGPTError(
                "ChatGPT completed without a text response. Try the task again."
            )
        usage = response.usage
        return ProviderCompletion(
            text,
            response.model,
            usage.input_tokens if usage else 0,
            usage.output_tokens if usage else 0,
            usage.total_tokens if usage else 0,
        )


class ResponsesAdapter:
    def __init__(
        self,
        config: ChatGPTConfig,
        *,
        sync_client_factory=OpenAI,
        async_client_factory=AsyncOpenAI,
        clock=time.monotonic,
    ):
        self.config = config
        self.sync_client_factory = sync_client_factory
        self.async_client_factory = async_client_factory
        self.clock = clock

    def probe(self, token, model):
        self._consume_sync(
            token,
            ProviderRequest(
                model,
                [{"role": "user", "content": "Reply with OK."}],
                timeout_seconds=self.config.probe_timeout_seconds,
            ),
        ).require_completed()

    def complete_sync(self, token, request):
        return self._consume_sync(token, request).finish()

    def _consume_sync(self, token, request):
        result = ResponseAccumulator()
        deadline = self.clock() + request.timeout_seconds
        try:
            with self.sync_client_factory(
                api_key=token,
                base_url=self.config.resource,
                max_retries=0,
                timeout=request.timeout_seconds,
            ) as client:
                with client.responses.create(**request_body(request)) as stream:
                    for event in stream:
                        if self.clock() > deadline:
                            raise ProviderTimeout(request.timeout_seconds, "backstop")
                        result.accept(event)
        except OpenAIError as exc:
            raise _failure(getattr(exc, "code", None)) from None
        return result

    async def complete(self, token, request):
        result = ResponseAccumulator()
        try:
            async with self.async_client_factory(
                api_key=token,
                base_url=self.config.resource,
                max_retries=0,
                timeout=request.timeout_seconds,
            ) as client:
                stream = await client.responses.create(**request_body(request))
                async with stream:
                    iterator = stream.__aiter__()
                    while True:
                        try:
                            event = (
                                await asyncio.wait_for(
                                    anext(iterator), timeout=request.stall_seconds
                                )
                                if request.stall_seconds > 0
                                else await anext(iterator)
                            )
                        except StopAsyncIteration:
                            break
                        except TimeoutError:
                            raise ProviderTimeout(
                                request.stall_seconds, "stall"
                            ) from None
                        result.accept(event)
        except OpenAIError as exc:
            raise _failure(getattr(exc, "code", None)) from None
        return result.finish()
