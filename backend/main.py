import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from database import init_db, get_db
from models import MagicWordCreate, MagicWordUpdate, MagicWordResponse, AlertResponse
import rss_monitor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# ── SSE接続管理 ─────────────────────────────────────────

sse_queues: set[asyncio.Queue] = set()


async def broadcast_alerts(alerts: list[dict]):
    """全SSEクライアントにアラートを送信する。"""
    if not sse_queues or not alerts:
        return
    payload = json.dumps({"type": "new_alerts", "alerts": alerts}, ensure_ascii=False)
    dead = set()
    for q in sse_queues:
        try:
            q.put_nowait(payload)
        except Exception:
            dead.add(q)
    sse_queues -= dead


async def self_ping():
    """Render無料プランのスリープを防止するため自分自身に定期リクエストを送る。"""
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        logger.info("RENDER_EXTERNAL_URL未設定 - セルフpingスキップ")
        return
    health_url = f"{url}/health"
    logger.info(f"セルフping開始: {health_url}")
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await asyncio.sleep(300)
                resp = await client.get(health_url, timeout=10)
                logger.debug(f"セルフping: {resp.status_code}")
            except Exception:
                logger.debug("セルフpingエラー（無視）")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリ起動時にDBを初期化し、RSS監視を開始する。"""
    await init_db()
    rss_monitor.on_new_alert = broadcast_alerts
    monitor_task = asyncio.create_task(rss_monitor.run_monitor(interval=10))
    ping_task = asyncio.create_task(self_ping())
    yield
    monitor_task.cancel()
    ping_task.cancel()


app = FastAPI(title="Stock Alert App", lifespan=lifespan)


# ── マジックワード CRUD API ──────────────────────────────


@app.get("/api/keywords", response_model=list[MagicWordResponse])
async def list_keywords():
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, keyword, category, is_active, created_at FROM magic_words ORDER BY created_at DESC"
        )
        return [dict(r) for r in rows]
    finally:
        await db.close()


@app.post("/api/keywords", response_model=MagicWordResponse, status_code=201)
async def create_keyword(body: MagicWordCreate):
    keyword = body.keyword.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="キーワードを入力してください")
    db = await get_db()
    try:
        existing = await db.execute_fetchall(
            "SELECT id FROM magic_words WHERE keyword = ?", (keyword,)
        )
        if existing:
            raise HTTPException(status_code=409, detail="このキーワードは既に登録されています")
        cursor = await db.execute(
            "INSERT INTO magic_words (keyword, category) VALUES (?, ?)",
            (keyword, body.category.strip()),
        )
        await db.commit()
        row = await db.execute_fetchall(
            "SELECT id, keyword, category, is_active, created_at FROM magic_words WHERE id = ?",
            (cursor.lastrowid,),
        )
        return dict(row[0])
    finally:
        await db.close()


@app.put("/api/keywords/{keyword_id}", response_model=MagicWordResponse)
async def update_keyword(keyword_id: int, body: MagicWordUpdate):
    db = await get_db()
    try:
        existing = await db.execute_fetchall(
            "SELECT id FROM magic_words WHERE id = ?", (keyword_id,)
        )
        if not existing:
            raise HTTPException(status_code=404, detail="キーワードが見つかりません")
        updates, params = [], []
        if body.keyword is not None:
            updates.append("keyword = ?"); params.append(body.keyword.strip())
        if body.category is not None:
            updates.append("category = ?"); params.append(body.category.strip())
        if body.is_active is not None:
            updates.append("is_active = ?"); params.append(body.is_active)
        if updates:
            params.append(keyword_id)
            await db.execute(f"UPDATE magic_words SET {', '.join(updates)} WHERE id = ?", params)
            await db.commit()
        row = await db.execute_fetchall(
            "SELECT id, keyword, category, is_active, created_at FROM magic_words WHERE id = ?",
            (keyword_id,),
        )
        return dict(row[0])
    finally:
        await db.close()


@app.delete("/api/keywords/{keyword_id}", status_code=204)
async def delete_keyword(keyword_id: int):
    db = await get_db()
    try:
        existing = await db.execute_fetchall(
            "SELECT id FROM magic_words WHERE id = ?", (keyword_id,)
        )
        if not existing:
            raise HTTPException(status_code=404, detail="キーワードが見つかりません")
        await db.execute("DELETE FROM magic_words WHERE id = ?", (keyword_id,))
        await db.commit()
    finally:
        await db.close()


# ── アラート API ─────────────────────────────────────────


@app.get("/api/alerts", response_model=list[AlertResponse])
async def list_alerts(limit: int = Query(default=50, le=200)):
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT a.id, a.keyword_id, m.keyword, m.category, a.title, a.source, a.url, a.matched_at, a.is_read
               FROM alerts a JOIN magic_words m ON a.keyword_id = m.id
               ORDER BY a.matched_at DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in rows]
    finally:
        await db.close()


@app.put("/api/alerts/{alert_id}/read")
async def mark_alert_read(alert_id: int):
    db = await get_db()
    try:
        await db.execute("UPDATE alerts SET is_read = 1 WHERE id = ?", (alert_id,))
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


@app.put("/api/alerts/read-all")
async def mark_all_alerts_read():
    db = await get_db()
    try:
        cursor = await db.execute("UPDATE alerts SET is_read = 1 WHERE is_read = 0")
        await db.commit()
        return {"ok": True, "count": cursor.rowcount}
    finally:
        await db.close()


# ── SSE (Server-Sent Events) ─────────────────────────────


@app.get("/api/stream")
async def sse_stream(request: Request):
    """SSEでアラートをリアルタイム配信する。ブラウザが自動再接続を行う。"""
    queue: asyncio.Queue = asyncio.Queue()
    sse_queues.add(queue)
    logger.info(f"SSE接続: {len(sse_queues)}クライアント")

    async def event_generator():
        try:
            while True:
                # クライアント切断を検知
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15)
                    yield {"event": "alert", "data": data}
                except asyncio.TimeoutError:
                    # 15秒ごとにkeep-aliveコメントを送信（接続維持）
                    yield {"comment": "keep-alive"}
        finally:
            sse_queues.discard(queue)
            logger.info(f"SSE切断: {len(sse_queues)}クライアント")

    return EventSourceResponse(event_generator())


# ── 静的ファイル・ページ ─────────────────────────────────

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}
