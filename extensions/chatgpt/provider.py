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
from .config import ChatGPTConfig, ChatGPTError

_ERRORS = {
    "subscription_sharing_usage_limit_exceeded": "Your ChatGPT usage limit was reached. Check ChatGPT Settings → Usage or wait for it to reset.",
    "subscription_sharing_usage_unavailable": "ChatGPT plan usage is unavailable for this account or workspace. Check ChatGPT Settings.",
}


def _failure(code=None):
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

    def accept(self, event):
        if event.type == "response.completed":
            self.completed = event.response
        elif event.type in {"response.failed", "response.incomplete", "error"}:
            error = getattr(getattr(event, "response", None), "error", None)
            raise _failure(getattr(error, "code", None) or getattr(event, "code", None))

    def finish(self):
        if self.completed is None:
            raise ChatGPTError(
                "ChatGPT’s response was interrupted before completion. Try the task again."
            )
        response = self.completed
        text = "".join(
            part.text
            for item in response.output
            if item.type == "message"
            for part in item.content
            if part.type == "output_text"
        )
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
        self.complete_sync(
            token,
            ProviderRequest(
                model,
                [{"role": "user", "content": "Reply with OK."}],
                timeout_seconds=self.config.probe_timeout_seconds,
            ),
        )

    def complete_sync(self, token, request):
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
        return result.finish()

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
