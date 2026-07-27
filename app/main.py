from __future__ import annotations

import asyncio
import logging
import math
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .device_profiles import DeviceProfile, list_profile_ids, load_device_profile
from .modbus import ModbusError, RegisterClient, build_client, decode_specs
from .simulator import EnergySimulator
from .storage import Storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
LOGGER = logging.getLogger("rootcastle-energy-scada")
SETTINGS = Settings.from_env()
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class Runtime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.profile: DeviceProfile = load_device_profile(settings.device_profile)
        self.storage = Storage(settings.database_path, settings.timezone, settings.max_expected_kw)
        self.simulator = EnergySimulator(settings.timezone)
        self.client: RegisterClient | None = None
        self.latest: dict[str, Any] | None = None
        self.latest_energy: dict[str, Any] = {}
        self.last_success_ts: float | None = None
        self.last_error: str | None = None
        self.consecutive_errors = 0
        self.samples_total = 0
        self.poll_errors_total = 0
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._poll_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._last_energy_poll = 0.0
        self._last_persist = 0.0

    async def start(self) -> None:
        if self.settings.mode == "simulator":
            await asyncio.to_thread(self.storage.seed_simulator)
        else:
            self.client = await asyncio.to_thread(build_client, self.settings)
        self._poll_task = asyncio.create_task(self._poll_loop(), name="energy-scada-poller")

    async def stop(self) -> None:
        self._stop.set()
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self.client:
            await asyncio.to_thread(self.client.close)
        await asyncio.to_thread(self.storage.close)

    async def _poll_loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                sample = await self._read_sample()
                self._validate_sample(sample)
                self.latest = sample
                self.last_success_ts = float(sample["timestamp"])
                self.last_error = None
                self.consecutive_errors = 0
                self.samples_total += 1
                if time.monotonic() - self._last_persist >= self.settings.persist_interval_s:
                    await asyncio.to_thread(self.storage.insert_sample, sample)
                    self._last_persist = time.monotonic()
                self._broadcast(sample)
            except Exception as exc:
                self.poll_errors_total += 1
                self.consecutive_errors += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                LOGGER.warning("poll_failed profile=%s error=%s count=%d", self.profile.profile_id, self.last_error, self.consecutive_errors)
                if self.consecutive_errors in {1, 3, 10}:
                    await asyncio.to_thread(
                        self.storage.add_event,
                        "warning" if self.consecutive_errors < 10 else "critical",
                        "modbus_poll_failed",
                        f"{self.profile.display_name} veri okuması başarısız",
                        {"profile": self.profile.profile_id, "error": self.last_error, "consecutive": self.consecutive_errors},
                    )
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.05, self.settings.poll_interval_s - elapsed))

    async def _read_sample(self) -> dict[str, Any]:
        now = time.time()
        if self.settings.mode == "simulator":
            return self.simulator.sample(now)
        if self.client is None:
            raise ModbusError("Modbus client is not initialized")
        live: dict[str, Any] = {}
        for block in self.profile.live_blocks:
            registers = await asyncio.to_thread(self.client.read_registers, block.function_code, block.start, block.count)
            live.update(decode_specs(registers, block.start, block.registers))
        if now - self._last_energy_poll >= self.settings.energy_poll_interval_s or not self.latest_energy:
            energy: dict[str, Any] = {}
            for block in self.profile.energy_blocks:
                registers = await asyncio.to_thread(self.client.read_registers, block.function_code, block.start, block.count)
                energy.update(decode_specs(registers, block.start, block.registers))
            self.latest_energy = energy
            self._last_energy_poll = now
        return {"timestamp": now, **live, **self.latest_energy}

    def _validate_sample(self, sample: dict[str, Any]) -> None:
        for key in self.profile.mandatory_fields:
            if key not in sample or not math.isfinite(float(sample[key])):
                raise ValueError(f"Invalid mandatory field: {key}")
        frequency = sample.get("frequency_hz")
        if frequency is not None and not 0.0 <= float(frequency) <= 100.0:
            raise ValueError("Frequency outside physical bounds")
        for key in ("voltage_l1_v", "voltage_l2_v", "voltage_l3_v", "current_l1_a", "current_l2_a", "current_l3_a"):
            if key in sample and float(sample[key]) < 0.0:
                raise ValueError(f"Negative unsigned measurement: {key}")

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def _broadcast(self, sample: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(sample)
            except asyncio.QueueFull:
                pass

    def health(self) -> dict[str, Any]:
        age = time.time() - self.last_success_ts if self.last_success_ts else None
        connected = age is not None and age <= max(10.0, self.settings.poll_interval_s * 3.0)
        return {
            "status": "ok" if connected else "degraded",
            "mode": self.settings.mode,
            "transport": self.settings.transport,
            "connected": connected,
            "device": self.profile.public_metadata(),
            "last_sample_age_s": round(age, 3) if age is not None else None,
            "last_error": self.last_error,
            "consecutive_errors": self.consecutive_errors,
            "samples_total": self.samples_total,
        }


RUNTIME = Runtime(SETTINGS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await RUNTIME.start()
    try:
        yield
    finally:
        await RUNTIME.stop()


app = FastAPI(title="Rootcastle Energy SCADA", version="2.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/v1/device-profile")
async def device_profile() -> JSONResponse:
    return JSONResponse(RUNTIME.profile.public_metadata())


@app.get("/api/v1/device-profiles")
async def device_profiles() -> JSONResponse:
    profiles = [load_device_profile(profile_id).public_metadata() for profile_id in list_profile_ids()]
    return JSONResponse({"profiles": profiles})


@app.get("/api/v1/live")
async def live() -> JSONResponse:
    if RUNTIME.latest is None:
        raise HTTPException(status_code=503, detail="No measurement available yet")
    return JSONResponse({"measurement": RUNTIME.latest, "health": RUNTIME.health()})


@app.get("/api/v1/history")
async def history(hours: int = Query(24, ge=1, le=2160), bucket_minutes: int = Query(15, ge=1, le=1440)) -> JSONResponse:
    data = await asyncio.to_thread(RUNTIME.storage.history, hours, bucket_minutes)
    return JSONResponse({"points": data, "hours": hours, "bucket_minutes": bucket_minutes})


@app.get("/api/v1/analytics")
async def analytics(days: int = Query(7, ge=1, le=90)) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(RUNTIME.storage.analytics, days))


@app.get("/api/v1/events")
async def events(limit: int = Query(20, ge=1, le=100)) -> JSONResponse:
    return JSONResponse({"events": await asyncio.to_thread(RUNTIME.storage.recent_events, limit)})


@app.get("/livez")
async def livez() -> JSONResponse:
    return JSONResponse({"alive": True})


@app.get("/healthz")
async def healthz() -> JSONResponse:
    data = RUNTIME.health()
    return JSONResponse(data, status_code=200 if data["status"] == "ok" else 503)


@app.get("/readyz")
async def readyz() -> JSONResponse:
    ready = RUNTIME.latest is not None
    return JSONResponse({"ready": ready}, status_code=200 if ready else 503)


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    connected = 1 if RUNTIME.health()["connected"] else 0
    body = "\n".join((
        "# HELP energy_scada_connected Whether fresh device data is available.",
        "# TYPE energy_scada_connected gauge",
        f"energy_scada_connected {connected}",
        "# HELP energy_scada_samples_total Accepted samples.",
        "# TYPE energy_scada_samples_total counter",
        f"energy_scada_samples_total {RUNTIME.samples_total}",
        "# HELP energy_scada_poll_errors_total Poll failures.",
        "# TYPE energy_scada_poll_errors_total counter",
        f"energy_scada_poll_errors_total {RUNTIME.poll_errors_total}",
        "",
    ))
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")


@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = RUNTIME.subscribe()
    try:
        if RUNTIME.latest is not None:
            await websocket.send_json({"measurement": RUNTIME.latest, "health": RUNTIME.health()})
        while True:
            try:
                sample = await asyncio.wait_for(queue.get(), timeout=15.0)
                await websocket.send_json({"measurement": sample, "health": RUNTIME.health()})
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "heartbeat", "health": RUNTIME.health()})
    except WebSocketDisconnect:
        pass
    finally:
        RUNTIME.unsubscribe(queue)
