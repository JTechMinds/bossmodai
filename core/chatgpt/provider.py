"""Responses adapter for ChatGPT plan usage; BossMod retains its agent loop."""
from __future__ import annotations

import asyncio
import json

from openai import AsyncOpenAI, OpenAI, OpenAIError

from core.chatgpt import ChatGPTError, RESOURCE


_ERRORS = {
    "subscription_sharing_usage_limit_exceeded": "Your ChatGPT usage limit was reached. Check ChatGPT Settings → Usage or wait for it to reset.",
    "subscription_sharing_usage_unavailable": "ChatGPT plan usage is unavailable for this account or workspace. Check ChatGPT Settings.",
}


def _failure(code: str | None = None) -> ChatGPTError:
    return ChatGPTError(_ERRORS.get(code, "ChatGPT couldn’t complete this request. Check your connection or sign in again in Extensions."))


def response_input(messages: list[dict]) -> list[dict]:
    """Translate text/image chat history without sending unsupported system items."""
    result = []
    for message in messages:
        role = message.get("role", "user")
        if role == "system":
            role = "developer"
        if role not in {"developer", "user", "assistant"}:
            raise ChatGPTError(f"ChatGPT connection cannot accept a {role} history message.")
        content = message.get("content", "")
        if isinstance(content, str):
            result.append({"role": role, "content": content})
            continue
        else:
            parts = []
            for part in content:
                if part.get("type") == "text":
                    parts.append({"type": "output_text" if role == "assistant" else "input_text", "text": part["text"]})
                elif part.get("type") == "image_url" and role != "assistant":
                    image = part["image_url"]
                    parts.append({"type": "input_image", "image_url": image["url"], "detail": image.get("detail", "auto")})
                else:
                    raise ChatGPTError("This attachment type is not supported by the ChatGPT connection.")
        if role == "assistant":
            result.append({"role": role, "content": "".join(part["text"] for part in parts)})
        else:
            result.append({"role": role, "content": parts})
    return result


def complete_sync(token: str, model: str, messages: list[dict], *, timeout: float = 30, extra: dict | None = None) -> str:
    """One streamed call for the connection probe and System AI helpers."""
    import time
    completed = None
    started = time.monotonic()
    options = extra or {}
    if set(options) - {"reasoning", "text"}:
        raise ChatGPTError("ChatGPT connection options support only reasoning and text settings.")
    try:
        with OpenAI(api_key=token, base_url=RESOURCE, max_retries=0, timeout=timeout) as client:
            with client.responses.create(model=model, input=response_input(messages),
                                         store=False, stream=True, **options) as stream:
                for event in stream:
                    if time.monotonic() - started > timeout:
                        raise ChatGPTError("ChatGPT’s request timed out. Please try again.")
                    if event.type == "response.completed":
                        completed = event.response
                    elif event.type in {"response.failed", "response.incomplete", "error"}:
                        error = getattr(getattr(event, "response", None), "error", None)
                        raise _failure(getattr(error, "code", None) or getattr(event, "code", None))
    except OpenAIError as exc:
        raise _failure(getattr(exc, "code", None)) from None
    if completed is None:
        raise ChatGPTError("ChatGPT’s response was interrupted before completion. Please try again.")
    text = "".join(part.text for item in completed.output if item.type == "message"
                   for part in item.content if part.type == "output_text")
    if not text.strip():
        raise ChatGPTError("ChatGPT completed without a text response. Please try again.")
    return text


def probe(token: str, model: str) -> None:
    """Verify actual model access before creating the managed connection."""
    complete_sync(token, model, [{"role": "user", "content": "Reply with OK."}])


async def completion(model: str, messages: list[dict], extra_body: str | None = None):
    from core import config
    from core.chatgpt.auth import access_token
    from core.llm.call_budget import budget, current_turn_lane
    from core.llm.client import LLMError, LLMResponse, LLMTimeoutError

    backstop = config.require_float("llm_request_timeout_seconds")
    stall = config.require_float("llm_stall_timeout_seconds")
    owned = None
    if current_turn_lane() is None:
        owned = await budget.acquire(kind="call", owner="chatgpt")
    try:
        async with asyncio.timeout(backstop):
            token = await asyncio.to_thread(access_token)
            kwargs = {"model": model, "input": response_input(messages), "store": False, "stream": True}
            if extra_body:
                try:
                    extra = json.loads(extra_body)
                except (ValueError, TypeError):
                    raise ChatGPTError("ChatGPT connection options must be valid JSON.") from None
                # This route explicitly forbids temperature, max_output_tokens,
                # previous_response_id, and hosted tools. Never forward arbitrary fields.
                if not isinstance(extra, dict) or set(extra) - {"reasoning", "text"}:
                    raise ChatGPTError("ChatGPT connection options support only reasoning and text settings.")
                kwargs.update(extra)
            async with AsyncOpenAI(api_key=token, base_url=RESOURCE, max_retries=0, timeout=backstop) as client:
                stream = await client.responses.create(**kwargs)
                completed = None
                async with stream:
                    iterator = stream.__aiter__()
                    while True:
                        try:
                            if stall > 0:
                                event = await asyncio.wait_for(anext(iterator), timeout=stall)
                            else:
                                event = await anext(iterator)
                        except StopAsyncIteration:
                            break
                        except TimeoutError:
                            raise LLMTimeoutError(stall, kind="stall") from None
                        if event.type == "response.completed":
                            completed = event.response
                        elif event.type in {"response.failed", "response.incomplete", "error"}:
                            error = getattr(getattr(event, "response", None), "error", None)
                            raise _failure(getattr(error, "code", None) or getattr(event, "code", None))
                if completed is None:
                    raise ChatGPTError("ChatGPT’s response was interrupted before completion. Try the task again.")
                text = "".join(part.text for item in completed.output if item.type == "message"
                               for part in item.content if part.type == "output_text")
                if not text.strip():
                    raise ChatGPTError("ChatGPT completed without a text response. Try the task again.")
                usage = completed.usage
                return LLMResponse(content=text, model=completed.model, prompt_tokens=usage.input_tokens if usage else 0,
                    completion_tokens=usage.output_tokens if usage else 0, total_tokens=usage.total_tokens if usage else 0,
                    finish_reason="stop")
    except TimeoutError:
        raise LLMTimeoutError(backstop, kind="backstop") from None
    except ChatGPTError as exc:
        raise LLMError(str(exc)) from None
    except OpenAIError as exc:
        raise LLMError(str(_failure(getattr(exc, "code", None)))) from None
    finally:
        if owned is not None:
            budget.release(owned)
