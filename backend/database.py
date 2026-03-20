import aiosqlite
import os
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "stock_alert.db"


async def get_db() -> aiosqlite.Connection:
    """DBコネクションを取得する。"""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db():
    """データベースとテーブルを初期化する。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS magic_words (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL UNIQUE,
                category TEXT DEFAULT '',
                is_active BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS rss_feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                last_checked DATETIME
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                source TEXT NOT NULL,
                url TEXT NOT NULL,
                matched_at DATETIME NOT NULL DEFAULT (datetime('now', 'localtime')),
                is_read BOOLEAN NOT NULL DEFAULT 0,
                FOREIGN KEY (keyword_id) REFERENCES magic_words(id) ON DELETE CASCADE
            )
        """)

        # デフォルトのRSSフィードを登録
        default_feeds = [
            ("TDnet適時開示", "https://webapi.yanoshin.jp/webapi/tdnet/list/recent.rss"),
            ("JPX適時開示(全件)", "https://www.release.tdnet.info/inbs/I_list_001_20260320.rss"),
            ("日経プレスリリース", "https://assets.nikkei.jp/release/rss/nikkei_release.rdf"),
            ("Reuters Japan", "https://assets.wor.jp/rss/rdf/reuters/top.rdf"),
        ]
        await db.executemany(
            "INSERT OR IGNORE INTO rss_feeds (name, url) VALUES (?, ?)",
            default_feeds,
        )

        # 推奨マジックワードを初期登録
        default_keywords = [
            # テーマ株
            ("AI", "テーマ"),
            ("人工知能", "テーマ"),
            ("半導体", "テーマ"),
            ("量子", "テーマ"),
            ("防衛", "テーマ"),
            ("宇宙", "テーマ"),
            ("データセンター", "テーマ"),
            ("再生医療", "テーマ"),
            ("GLP-1", "テーマ"),
            ("ペロブスカイト", "テーマ"),
            ("核融合", "テーマ"),
            # 業績インパクト
            ("上方修正", "業績"),
            ("業績予想の修正", "業績"),
            ("特別利益", "業績"),
            ("増配", "業績"),
            ("復配", "業績"),
            ("黒字転換", "業績"),
            ("過去最高", "業績"),
            # コーポレートアクション
            ("株式分割", "IR"),
            ("自社株買い", "IR"),
            ("自己株式の取得", "IR"),
            ("TOB", "IR"),
            ("MBO", "IR"),
            ("完全子会社", "IR"),
            ("資本業務提携", "IR"),
            ("第三者割当", "IR"),
            ("新株予約権", "IR"),
            # 受注・契約
            ("受注", "受注"),
            ("契約締結", "受注"),
            ("採用決定", "受注"),
            ("共同開発", "受注"),
            # 注意系
            ("下方修正", "注意"),
            ("債務超過", "注意"),
            ("不適正意見", "注意"),
            ("上場廃止", "注意"),
        ]

        await db.executemany(
            "INSERT OR IGNORE INTO magic_words (keyword, category) VALUES (?, ?)",
            default_keywords,
        )

        await db.commit()
