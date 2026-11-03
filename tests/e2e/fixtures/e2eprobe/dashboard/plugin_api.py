"""Harness probe plugin API: who is calling, an in-process counter store the Desktop part writes to."""
import os
from fastapi import APIRouter, Request

router = APIRouter()
_STORE = {"pongs": [], "ticks": 0}


@router.get("/who")
async def who(request: Request):
    from hermes_constants import get_hermes_home
    p = getattr(request.state, "native_http_principal", None)
    return {"home": str(get_hermes_home()), "pid": os.getpid(),
            "principal": dict(p, capabilities=sorted(p["capabilities"])) if p else None}


@router.post("/pong")
async def pong(request: Request):
    _STORE["pongs"].append(await request.json())
    return {"n": len(_STORE["pongs"])}


@router.post("/tick")
async def tick():
    _STORE["ticks"] += 1
    return {"ticks": _STORE["ticks"]}


@router.get("/store")
async def store():
    return _STORE
