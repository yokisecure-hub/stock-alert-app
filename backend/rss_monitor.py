"""RSS監視エンジン - フィード巡回・キーワードマッチング"""

import asyncio
import logging
import re
from datetime import datetime

import feedparser

from database import get_db

logger = logging.getLogger(__name__)

# 巡回済みURLを保持（重複アラート防止）
_seen_urls: set[str] = set()

# 新着アラートのコールバック（WebSocket通知用、Step 4で設定）
on_new_alert = None

# JPX適時開示の日付付きURLを当日に補正する
_JPX_DATE_RE = re.compile(r"(I_list_001_)\d{8}(\.rss)")


def _fix_jpx_url(url: str) -> str:
    today = datetime.now().strftime("%Y%m%d")
    return _JPX_DATE_RE.sub(rf"\g<1>{today}\2", url)


async def fetch_active_keywords() -> list[dict]:
    """有効なマジックワードを取得する。"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, keyword FROM magic_words WHERE is_active = 1"
        )
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def fetch_active_feeds() -> list[dict]:
    """有効なRSSフィードを取得する。"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, name, url FROM rss_feeds WHERE is_active = 1"
        )
        return [dict(r) for r in rows]
    finally:
        await db.close()


def parse_feed(url: str) -> list[dict]:
    """RSSフィードをパースしてエントリ一覧を返す。"""
    d = feedparser.parse(url)
    entries = []
    for e in d.entries:
        entries.append({
            "title": e.get("title", ""),
            "link": e.get("link", ""),
            "summary": e.get("summary", ""),
            "published": e.get("published", ""),
        })
    return entries


def match_keywords(text: str, keywords: list[dict]) -> list[dict]:
    """テキストにマッチするキーワードを返す。"""
    text_lower = text.lower()
    matched = []
    for kw in keywords:
        if kw["keyword"].lower() in text_lower:
            matched.append(kw)
    return matched


async def save_alert(keyword_id: int, title: str, source: str, url: str) -> dict | None:
    """アラートをDBに保存する。既に同じURLとキーワードの組み合わせがあればスキップ。"""
    db = await get_db()
    try:
        existing = await db.execute_fetchall(
            "SELECT id FROM alerts WHERE keyword_id = ? AND url = ?",
            (keyword_id, url),
        )
        if existing:
            return None

        cursor = await db.execute(
            "INSERT INTO alerts (keyword_id, title, source, url) VALUES (?, ?, ?, ?)",
            (keyword_id, title, source, url),
        )
        await db.commit()

        row = await db.execute_fetchall(
            """SELECT a.id, a.keyword_id, m.keyword, m.category, a.title, a.source, a.url, a.matched_at, a.is_read
               FROM alerts a JOIN magic_words m ON a.keyword_id = m.id
               WHERE a.id = ?""",
            (cursor.lastrowid,),
        )
        return dict(row[0]) if row else None
    finally:
        await db.close()


async def update_feed_checked(feed_id: int):
    """フィードの最終巡回日時を更新する。"""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE rss_feeds SET last_checked = ? WHERE id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), feed_id),
        )
        await db.commit()
    finally:
        await db.close()


async def _fetch_feed(feed: dict) -> tuple[dict, list[dict]]:
    """1フィードを取得する（並列実行用）。"""
    url = _fix_jpx_url(feed["url"])
    try:
        entries = await asyncio.to_thread(parse_feed, url)
        logger.info(f"[{feed['name']}] {len(entries)}件取得")
        return feed, entries
    except Exception:
        logger.exception(f"フィード巡回エラー: {feed['name']}")
        return feed, []


async def poll_once() -> list[dict]:
    """全フィードを並列巡回し、新着アラートを返す。"""
    keywords = await fetch_active_keywords()
    if not keywords:
        return []

    feeds = await fetch_active_feeds()
    new_alerts = []

    # 全フィードを並列で取得
    results = await asyncio.gather(*[_fetch_feed(f) for f in feeds])

    for feed, entries in results:
        for entry in entries:
            url = entry["link"]
            if url in _seen_urls:
                continue

            # タイトル＋サマリーでマッチング
            search_text = f"{entry['title']} {entry['summary']}"
            matched = match_keywords(search_text, keywords)

            for kw in matched:
                alert = await save_alert(
                    keyword_id=kw["id"],
                    title=entry["title"],
                    source=feed["name"],
                    url=url,
                )
                if alert:
                    new_alerts.append(alert)
                    logger.info(f"ALERT: [{kw['keyword']}] {entry['title']}")

            _seen_urls.add(url)

        await update_feed_checked(feed["id"])

    return new_alerts


async def run_monitor(interval: int = 30):
    """指定間隔（秒）でRSSフィードを巡回し続ける。"""
    logger.info(f"RSS監視開始（{interval}秒間隔）")

    while True:
        try:
            new_alerts = await poll_once()
            if new_alerts and on_new_alert:
                await on_new_alert(new_alerts)
        except Exception:
            logger.exception("RSS監視ループエラー")

        await asyncio.sleep(interval)
