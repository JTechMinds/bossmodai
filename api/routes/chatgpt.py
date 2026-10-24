"""Operator-only ChatGPT connection actions; protected by the local API gate."""
import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from api.websocket import manager
from core.chatgpt import ChatGPTError
from core.chatgpt import auth

router = APIRouter(prefix="/extensions/chatgpt")


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str | None = None


class ModelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str


async def _run(function, *args):
    try:
        return await asyncio.to_thread(function, *args)
    except ChatGPTError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/account")
async def account_status():
    result = await _run(auth.status)
    if result["connected"]:
        await manager.broadcast_operator_invalidate(["connections"])
    return result


@router.post("/login")
async def login(body: LoginBody):
    return await _run(auth.begin_login, body.account_id)


@router.delete("/login", status_code=204)
async def cancel_login():
    await _run(auth.cancel_login)


@router.post("/logout")
async def logout():
    result = await _run(auth.sign_out)
    await manager.broadcast_operator_invalidate(["connections"])
    return result


@router.put("/model")
async def set_model(body: ModelBody):
    await _run(auth.select_model, body.model)
    await manager.broadcast_operator_invalidate(["connections"])
    return await _run(auth.status)
