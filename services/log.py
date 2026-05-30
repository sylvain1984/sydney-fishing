# ============================================================
# services/log.py — 渔获日记服务
# 复用 stats.py 的数据库连接模式（本地 SQLite / Turso 自动切换）
# ============================================================

import os
import json
import base64
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

SYD_TZ = ZoneInfo("Australia/Sydney")

TURSO_URL = os.environ.get("TURSO_DATABASE_URL")
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN")
USE_TURSO = bool(TURSO_URL and TURSO_TOKEN)

APP_DATA_DIR = Path(
    os.environ.get("APP_DATA_DIR", str(Path(__file__).parent.parent / "data"))
)
DB_PATH = APP_DATA_DIR / "stats.db"
MAX_PHOTOS = 4
MAX_PHOTO_BYTES = 3 * 1024 * 1024


def _get_connection():
    if USE_TURSO:
        import libsql_client
        return libsql_client.create_client_sync(url=TURSO_URL, auth_token=TURSO_TOKEN)
    else:
        import sqlite3
        return sqlite3.connect(DB_PATH)


@contextmanager
def _db_cursor():
    conn = _get_connection()
    try:
        if USE_TURSO:
            yield conn
        else:
            yield conn.cursor()
        conn.commit()
    finally:
        conn.close()


def _execute(sql: str, params: tuple = ()):
    with _db_cursor() as cursor:
        cursor.execute(sql, params)
        if not USE_TURSO:
            return cursor.lastrowid


def _query_one(sql: str, params: tuple = ()):
    with _db_cursor() as cursor:
        if USE_TURSO:
            result = cursor.execute(sql, params)
            rows = result.rows
            return rows[0] if rows else None
        else:
            cursor.execute(sql, params)
            return cursor.fetchone()


def _query_all(sql: str, params: tuple = ()):
    with _db_cursor() as cursor:
        if USE_TURSO:
            result = cursor.execute(sql, params)
            return result.rows
        else:
            cursor.execute(sql, params)
            return cursor.fetchall()


def _init_db():
    if not USE_TURSO:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _execute('''
        CREATE TABLE IF NOT EXISTS fishing_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fish_date DATE NOT NULL,
            spot_name TEXT NOT NULL,
            author TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            fish_caught TEXT DEFAULT '[]',
            photos TEXT DEFAULT '[]',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    _execute('''
        CREATE TABLE IF NOT EXISTS log_likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(entry_id, session_id)
        )
    ''')
    _execute(
        "CREATE INDEX IF NOT EXISTS idx_log_likes_entry ON log_likes(entry_id)"
    )


_init_db()


def add_entry(
    fish_date: str,
    spot_name: str,
    author: str,
    notes: str,
    fish_caught: list,
    photos: list,
) -> int:
    """添加一条渔获记录，photos 为 bytes 列表。返回新记录 id。"""
    safe_photos = [
        p for p in (photos or [])[:MAX_PHOTOS]
        if len(p) <= MAX_PHOTO_BYTES
    ]
    fish_json = json.dumps(fish_caught, ensure_ascii=False)
    photos_json = json.dumps(
        [base64.b64encode(p).decode() for p in safe_photos],
        ensure_ascii=False,
    )
    created_at = datetime.now(SYD_TZ).isoformat()
    rowid = _execute(
        '''INSERT INTO fishing_log
               (fish_date, spot_name, author, notes, fish_caught, photos, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (fish_date, spot_name, author or "", notes or "", fish_json, photos_json, created_at),
    )
    if rowid is None:
        result = _query_one("SELECT id FROM fishing_log ORDER BY id DESC LIMIT 1")
        return result[0] if result else None
    return rowid


def get_entries(limit: int = 100) -> list:
    """返回最新 limit 条记录，每条为 dict。photos 字段为 bytes 列表。"""
    rows = _query_all(
        '''SELECT id, fish_date, spot_name, author, notes, fish_caught, photos, created_at
           FROM fishing_log
           ORDER BY fish_date DESC, id DESC
           LIMIT ?''',
        (limit,),
    )
    entries = []
    for row in rows:
        entries.append({
            "id": row[0],
            "fish_date": row[1],
            "spot_name": row[2],
            "author": row[3] or "",
            "notes": row[4] or "",
            "fish_caught": json.loads(row[5] or "[]"),
            "photos": [base64.b64decode(p) for p in json.loads(row[6] or "[]")],
            "created_at": row[7] or "",
        })
    return entries


def delete_entry(entry_id: int) -> None:
    """删除指定记录。"""
    _execute("DELETE FROM fishing_log WHERE id = ?", (entry_id,))


# ── 日记点赞 ─────────────────────────────────────────────────────────────

def get_log_like_count(entry_id: int) -> int:
    """返回某条日记的点赞总数。"""
    result = _query_one(
        "SELECT COUNT(*) FROM log_likes WHERE entry_id = ?",
        (entry_id,),
    )
    return result[0] if result else 0


def has_user_liked_log(entry_id: int, session_id: str) -> bool:
    """检查当前会话是否已对该日记点赞。"""
    result = _query_one(
        "SELECT 1 FROM log_likes WHERE entry_id = ? AND session_id = ?",
        (entry_id, session_id),
    )
    return result is not None


def toggle_log_like(entry_id: int, session_id: str) -> tuple[int, bool]:
    """切换点赞状态，返回 (当前点赞数, 是否为点赞)。"""
    if has_user_liked_log(entry_id, session_id):
        _execute(
            "DELETE FROM log_likes WHERE entry_id = ? AND session_id = ?",
            (entry_id, session_id),
        )
        return get_log_like_count(entry_id), False
    else:
        try:
            _execute(
                "INSERT INTO log_likes (entry_id, session_id) VALUES (?, ?)",
                (entry_id, session_id),
            )
        except Exception:
            # 并发重复插入时 UNIQUE 约束冲突，视为已点赞
            pass
        return get_log_like_count(entry_id), True


def get_like_stats_bulk(
    entry_ids: list[int], session_id: str
) -> dict[int, tuple[int, bool]]:
    """批量返回 {entry_id: (like_count, has_liked)}，单次查询替代 N×2 次。"""
    if not entry_ids:
        return {}
    placeholders = ",".join("?" * len(entry_ids))
    rows = _query_all(
        f"SELECT entry_id, COUNT(*), MAX(session_id = ?) "
        f"FROM log_likes WHERE entry_id IN ({placeholders}) GROUP BY entry_id",
        (session_id, *entry_ids),
    )
    result: dict[int, tuple[int, bool]] = {eid: (0, False) for eid in entry_ids}
    for row in rows:
        result[row[0]] = (int(row[1]), bool(row[2]))
    return result
