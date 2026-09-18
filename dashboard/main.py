from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
import aiohttp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from bot.services.storage import Storage
from bot.services.oauth import (
    OAuthError, OAuthSettings, XaiOAuthClient, complete_oauth_callback,
)
from dashboard.queries import (
    read_backtest, read_funnel, read_open_positions, read_recent_decisions, read_stats,
)
from dashboard.metrics import render_metrics

WINDOWS = {"1h": 3600, "24h": 86400, "7d": 7 * 86400}
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(db_path: str | None = None) -> FastAPI:
    resolved_db_path = db_path or os.getenv("DB_PATH", "pumpguard.db")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        storage = Storage(resolved_db_path)
        await storage.connect_readonly()
        app.state.storage = storage
        try:
            yield
        finally:
            await storage.close()

    app = FastAPI(title="PumpGuard Dashboard", lifespan=lifespan)

    @app.get("/", response_class=HTMLResponse)
    async def funnel_page(request: Request, window: str = "24h"):
        if window not in WINDOWS:
            raise HTTPException(status_code=400, detail="window must be 1h, 24h, or 7d")
        storage = request.app.state.storage
        return TEMPLATES.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "page": "funnel", "window": window, "windows": WINDOWS,
                "stats": await read_stats(storage),
                "funnel": await read_funnel(storage, WINDOWS[window]),
                "backtest": await read_backtest(storage, WINDOWS[window]),
            },
        )

    @app.get("/positions", response_class=HTMLResponse)
    async def positions_page(request: Request):
        return TEMPLATES.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "page": "positions",
                "positions": await read_open_positions(request.app.state.storage),
            },
        )

    @app.get("/api/stats")
    async def api_stats(request: Request):
        return await read_stats(request.app.state.storage)

    @app.get("/api/decisions")
    async def api_decisions(request: Request, limit: int = 20, mint: str | None = None):
        """Why was each token bought, skipped or abstained on? Full decision trail."""
        return await read_recent_decisions(request.app.state.storage, limit, mint)

    @app.get("/oauth/callback", response_class=HTMLResponse)
    async def oauth_callback(code: str = "", state: str = "", error: str = ""):
        if error:
            raise HTTPException(status_code=400, detail="xAI authorization was denied")
        if not code or not state:
            raise HTTPException(status_code=400, detail="missing OAuth code or state")
        settings = OAuthSettings(
            os.getenv("XAI_OAUTH_CLIENT_ID", ""),
            os.getenv("XAI_OAUTH_CLIENT_SECRET", ""),
            os.getenv("XAI_OAUTH_REDIRECT_URI", ""),
            os.getenv("OAUTH_ENCRYPTION_KEY", ""),
        )
        try:
            client = XaiOAuthClient(settings)
            # Analytics routes retain their SQLite mode=ro connection. This narrow
            # callback uses a short-lived writer solely for the OAuth tables.
            writer = Storage(resolved_db_path)
            await writer.connect()
            try:
                async with aiohttp.ClientSession() as session:
                    await complete_oauth_callback(writer, client, session, state, code)
            finally:
                await writer.close()
        except OAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return HTMLResponse(
            "<h1>Grok connected</h1><p>You can close this page and return to Telegram.</p>"
        )

    @app.get("/metrics")
    async def metrics(request: Request):
        return Response(
            await render_metrics(request.app.state.storage),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "dashboard.main:app", host="0.0.0.0",
        port=int(os.getenv("DASHBOARD_PORT", "8000")),
    )
