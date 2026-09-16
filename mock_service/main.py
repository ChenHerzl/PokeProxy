from __future__ import annotations

from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings

from pokeproxy.config import PokemonJSON
from pokeproxy.http import InputError, read_body


class MockSettings(BaseSettings):
    mock_max_receipts: int = Field(default=1000, gt=0)
    mock_max_body_bytes: int = Field(default=1_048_576, gt=0)
    mock_upload_timeout: float = Field(default=10.0, gt=0, allow_inf_nan=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = MockSettings()
    app.state.receipts = deque(maxlen=app.state.settings.mock_max_receipts)
    yield


app = FastAPI(title="Mock Downstream Service", lifespan=lifespan)


@app.post("/pokemon")
async def receive_pokemon(request: Request):
    settings = request.app.state.settings
    try:
        raw = await read_body(
            request, settings.mock_max_body_bytes, settings.mock_upload_timeout
        )
        pokemon = PokemonJSON.model_validate_json(raw, strict=True)
    except InputError as exc:
        return JSONResponse({"error": exc.message}, status_code=exc.status)
    except ValidationError:
        return JSONResponse({"error": "invalid Pokemon JSON"}, status_code=400)
    correlation = request.headers.get("x-request-id", "")
    reason = request.headers.get("x-grd-reason", "unknown")
    if len(correlation) > 64 or len(reason) > 1024:
        return JSONResponse({"error": "receipt metadata too large"}, status_code=400)
    request.app.state.receipts.append(
        {"pokemon": pokemon.model_dump(), "reason": reason, "request_id": correlation}
    )
    return {"status": "received"}


@app.get("/received")
async def get_received(request: Request, request_id: str | None = None) -> list[dict]:
    return [
        r
        for r in request.app.state.receipts
        if request_id is None or r["request_id"] == request_id
    ]


@app.delete("/received")
async def clear_received(request: Request) -> dict[str, str]:
    request.app.state.receipts.clear()
    return {"status": "cleared"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "alive"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001)
