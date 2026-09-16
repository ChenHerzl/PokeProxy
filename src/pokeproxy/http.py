"""Small shared HTTP input boundaries for proxy and verification mock."""

import asyncio
import re
import uuid
from http.cookiejar import DefaultCookiePolicy

from fastapi import Request


class InputError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


def request_id(request: Request) -> str:
    supplied = request.headers.get("x-request-id", "")
    return (
        supplied if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", supplied) else uuid.uuid4().hex
    )


async def read_body(request: Request, limit: int, budget: float) -> bytes:
    lengths = request.headers.getlist("content-length")
    if lengths and (len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,20}", lengths[0])):
        raise InputError(400, "invalid Content-Length")
    declared = int(lengths[0]) if lengths else None
    if declared is not None and declared > limit:
        raise InputError(413, "payload too large")
    body = bytearray()
    try:
        async with asyncio.timeout(budget):
            async for chunk in request.stream():
                if len(body) + len(chunk) > limit:
                    raise InputError(413, "payload too large")
                body.extend(chunk)
    except TimeoutError:
        raise InputError(408, "request body timeout") from None
    if declared is not None and declared != len(body):
        raise InputError(400, "Content-Length does not match body")
    return bytes(body)


class RejectCookies(DefaultCookiePolicy):
    """The proxy must not retain session cookies between unrelated callers."""

    def set_ok(self, cookie, request) -> bool:
        return False
