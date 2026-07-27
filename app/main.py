from __future__ import annotations

import asyncio
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

APP_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = APP_DIR / "static"
MODE = os.getenv("SCADA_MODE", "simulator")
PROFILE = os.getenv("DEVICE_PROFILE", "entes-mpr53s")

app = FastAPI(title="Rootcastle Energy SCADA", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def sample() -> dict[str, object]:
    now = time.time()
    phase = now / 12.0
    active_kw = 62.0 + 12.0 * math.sin(phase) + 4.0 * math.sin(phase * 0.37)
    voltage = [230.0 + 2.0 * math.sin(phase + offset) for offset in (0.0, 2.1, 4.2)]
    current = [max(0.0, active_kw * 1000 / (3 * value * 0.93)) for value in voltage]
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": MODE,
        "profile": PROFILE,
        "quality": "simulated" if MODE == "simulator" else "device",
        "voltage_v": voltage,
        "current_a": current,
        "active_power_kw": round(active_kw, 2),
        "reactive_inductive_kvar": round(max(0.0, 14 + 5 * math.sin(phase * 0.7)), 2),
        "reactive_capacitive_kvar": round(max(0.0, 4 + 3 * math.cos(phase * 0.5)), 2),
        "apparent_power_kva": round(active_kw / 0.93, 2),
        "power_factor": 0.93,
        "frequency_hz": round(50.0 + 0.03 * math.sin(phase), 2),
        "thd_voltage_pct": [2.1, 2.3, 2.0],
        "thd_current_pct": [7.2, 7.6, 6.9],
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/v1/live")
def live() -> JSONResponse:
    return JSONResponse(sample())


@app.get("/api/v1/device-profile")
def device_profile() -> dict[str, object]:
    return {
        "id": PROFILE,
        "verification": "verified" if PROFILE == "entes-mpr53s" else "experimental",
        "read_only": True,
        "allowed_function_codes": [3, 4],
    }


@app.get("/livez")
def livez() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    return {"status": "ready"}


@app.get("/healthz")
def healthz() -> dict[str, object]:
    return {"status": "healthy", "mode": MODE, "profile": PROFILE}


@app.get("/metrics")
def metrics() -> PlainTextResponse:
    data = sample()
    body = (
        "# HELP rootcastle_scada_up Process health\n"
        "# TYPE rootcastle_scada_up gauge\n"
        "rootcastle_scada_up 1\n"
        "# HELP rootcastle_scada_active_power_kw Active power\n"
        "# TYPE rootcastle_scada_active_power_kw gauge\n"
        f"rootcastle_scada_active_power_kw {data['active_power_kw']}\n"
    )
    return PlainTextResponse(body)


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(sample())
            await asyncio.sleep(2)
    finally:
        await websocket.close()
