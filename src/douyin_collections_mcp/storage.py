"""Private, account-scoped cache and durable collection plans.

This module never logs into Douyin or performs remote changes. Memberships describe
what was observed at sync time; a missing item in a later page does not delete it.
"""

# Validation deliberately uses ValueError consistently at the MCP boundary.
# ruff: noqa: TRY004

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

OPERATION_STATUSES = frozenset({"pending", "running", "completed", "failed", "uncertain"})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"{label} must be a nonempty string of at most 256 characters")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{label} must not contain control characters")
    return value


def _source(value: str) -> str:
    if value not in ("favorites", "likes"):
        if not isinstance(value, str) or not value.startswith("folder:"):
            raise ValueError("source must be favorites, likes, or folder:<id>")
        _identifier(value[7:], "folder id")
    return value


def _folder_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("folder_name must contain 1 to 15 characters")
    # HTML maxLength counts UTF-16 code units, so most emoji count as two.
    if len(value.strip().encode("utf-16-le")) // 2 > 15:
        raise ValueError("folder_name must fit the platform's 15-character limit")
    if any(ord(char) < 32 for char in value):
        raise ValueError("folder_name must not contain control characters")
    return value.strip()


def _text(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def _json(value: dict, label: str) -> str:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"{label} must be JSON serializable") from exc


class Store:
    """SQLite persistence, safe to call from several server worker threads."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.data_dir.chmod(0o700)
        self.db_path = self.data_dir / "collections.sqlite3"
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.db_path, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        self._lock = RLock()
        self._db = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS videos (
                account_id TEXT NOT NULL,
                video_id TEXT NOT NULL,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                url TEXT NOT NULL,
                kind TEXT NOT NULL,
                extra_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (account_id, video_id)
            );
            CREATE TABLE IF NOT EXISTS video_sources (
                account_id TEXT NOT NULL,
                video_id TEXT NOT NULL,
                source TEXT NOT NULL,
                PRIMARY KEY (account_id, video_id, source),
                FOREIGN KEY (account_id, video_id)
                    REFERENCES videos(account_id, video_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS video_sources_lookup
                ON video_sources(account_id, source, video_id);
            CREATE TABLE IF NOT EXISTS folders (
                account_id TEXT NOT NULL,
                folder_id TEXT NOT NULL,
                name TEXT NOT NULL,
                count INTEGER,
                PRIMARY KEY (account_id, folder_id)
            );
            CREATE TABLE IF NOT EXISTS plans (
                plan_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                mode TEXT NOT NULL CHECK (mode = 'add'),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operations (
                plan_id TEXT NOT NULL REFERENCES plans(plan_id) ON DELETE CASCADE,
                operation_index INTEGER NOT NULL,
                video_id TEXT NOT NULL,
                folder_name TEXT NOT NULL,
                status TEXT NOT NULL CHECK
                    (status IN ('pending', 'running', 'completed', 'failed', 'uncertain')),
                detail_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (plan_id, operation_index),
                UNIQUE (plan_id, video_id, folder_name)
            );
            """
        )

    def upsert_videos(self, account_id: str, source: str, videos: list[dict]) -> None:
        account_id = _identifier(account_id, "account_id")
        source = _source(source)
        if not isinstance(videos, list):
            raise ValueError("videos must be a list")
        now = _now()
        rows = []
        for item in videos:
            if not isinstance(item, dict):
                raise ValueError("each video must be an object")
            rows.append(
                (
                    account_id,
                    _identifier(item.get("id"), "video id"),
                    _text(item.get("title", ""), "title"),
                    _text(item.get("author", ""), "author"),
                    _text(item.get("url", ""), "url"),
                    _text(item.get("kind", "video"), "kind"),
                    _json(item.get("extra", {}), "extra"),
                    now,
                )
            )
        with self._lock, self._db:
            self._db.executemany(
                """INSERT INTO videos VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, video_id) DO UPDATE SET
                    title=excluded.title, author=excluded.author, url=excluded.url,
                    kind=excluded.kind, extra_json=excluded.extra_json,
                    updated_at=excluded.updated_at""",
                rows,
            )
            self._db.executemany(
                "INSERT OR IGNORE INTO video_sources VALUES (?, ?, ?)",
                [(account_id, row[1], source) for row in rows],
            )

    def list_videos(
        self,
        account_id: str,
        source: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        account_id = _identifier(account_id, "account_id")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        if type(offset) is not int or offset < 0 or offset > 2**63 - 1:
            raise ValueError("offset must be a nonnegative SQLite integer")
        if query is not None and not isinstance(query, str):
            raise ValueError("query must be a string")
        clauses = ["v.account_id = ?"]
        params: list = [account_id]
        if source is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM video_sources s WHERE s.account_id = v.account_id "
                "AND s.video_id = v.video_id AND s.source = ?)"
            )
            params.append(_source(source))
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append(
                "(v.title LIKE ? ESCAPE '\\' OR v.author LIKE ? ESCAPE '\\' "
                "OR v.video_id LIKE ? ESCAPE '\\')"
            )
            params.extend([f"%{escaped}%"] * 3)
        params.extend([limit, offset])
        # Only the fixed clauses above are interpolated; all caller input is bound.
        statement = (
            "SELECT v.* FROM videos v WHERE "
            + " AND ".join(clauses)
            + " ORDER BY v.updated_at DESC, v.video_id ASC LIMIT ? OFFSET ?"
        )
        with self._lock:
            rows = self._db.execute(statement, params).fetchall()
            return self._decode_videos(account_id, rows)

    def get_videos_by_ids(self, account_id: str, video_ids: list[str]) -> list[dict]:
        """Return cached matches in requested order, omitting duplicate or missing IDs."""
        account_id = _identifier(account_id, "account_id")
        if not isinstance(video_ids, list) or len(video_ids) > 200:
            raise ValueError("video_ids must be a list of at most 200 IDs")
        normalized = list(dict.fromkeys(_identifier(value, "video id") for value in video_ids))
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM videos WHERE account_id=? AND video_id IN ({placeholders})",
                [account_id, *normalized],
            ).fetchall()
            decoded = {row["id"]: row for row in self._decode_videos(account_id, rows)}
            return [decoded[video_id] for video_id in normalized if video_id in decoded]

    def _decode_videos(self, account_id: str, rows: list[sqlite3.Row]) -> list[dict]:
        """Decode rows while the caller holds the database lock."""
        output = []
        for row in rows:
            sources = self._db.execute(
                "SELECT source FROM video_sources WHERE account_id=? AND video_id=? ORDER BY source",
                (account_id, row["video_id"]),
            ).fetchall()
            output.append(
                {
                    "id": row["video_id"],
                    "title": row["title"],
                    "author": row["author"],
                    "url": row["url"],
                    "kind": row["kind"],
                    "extra": json.loads(row["extra_json"]),
                    "sources": [item["source"] for item in sources],
                    "updated_at": row["updated_at"],
                }
            )
        return output

    def upsert_folders(self, account_id: str, folders: list[dict]) -> None:
        account_id = _identifier(account_id, "account_id")
        if not isinstance(folders, list):
            raise ValueError("folders must be a list")
        rows = []
        for folder in folders:
            if not isinstance(folder, dict):
                raise ValueError("each folder must be an object")
            count = folder.get("count")
            if count is not None and (type(count) is not int or not 0 <= count <= 2**63 - 1):
                raise ValueError("folder count must be a nonnegative integer or null")
            rows.append(
                (
                    account_id,
                    _identifier(folder.get("id"), "folder id"),
                    _text(folder.get("name"), "folder name"),
                    count,
                )
            )
        with self._lock, self._db:
            self._db.executemany(
                """INSERT INTO folders VALUES (?, ?, ?, ?)
                ON CONFLICT(account_id, folder_id) DO UPDATE SET
                    name=excluded.name, count=excluded.count""",
                rows,
            )

    def list_folders(self, account_id: str) -> list[dict]:
        account_id = _identifier(account_id, "account_id")
        with self._lock:
            rows = self._db.execute(
                "SELECT folder_id AS id, name, count FROM folders WHERE account_id=? "
                "ORDER BY name, folder_id",
                (account_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_plan(self, account_id: str, assignments: list[dict], mode: str = "add") -> dict:
        account_id = _identifier(account_id, "account_id")
        if mode != "add":
            raise ValueError("only add mode is supported")
        if not isinstance(assignments, list) or not 1 <= len(assignments) <= 200:
            raise ValueError("assignments must contain between 1 and 200 operations")
        normalized = []
        seen = set()
        for assignment in assignments:
            if not isinstance(assignment, dict):
                raise ValueError("each assignment must be an object")
            video_id = _identifier(assignment.get("video_id"), "video_id")
            folder_name = _folder_name(assignment.get("folder_name"))
            pair = (video_id, folder_name)
            if pair in seen:
                raise ValueError("duplicate video and folder assignment")
            seen.add(pair)
            normalized.append(pair)
        plan_id, now = str(uuid4()), _now()
        with self._lock, self._db:
            for video_id, _ in normalized:
                cached = self._db.execute(
                    "SELECT 1 FROM video_sources WHERE account_id=? AND video_id=? "
                    "AND (source IN ('favorites', 'likes') OR source LIKE 'folder:%') LIMIT 1",
                    (account_id, video_id),
                ).fetchone()
                if cached is None:
                    raise ValueError(f"video {video_id!r} is not cached for this account")
            self._db.execute(
                "INSERT INTO plans VALUES (?, ?, ?, ?)", (plan_id, account_id, mode, now)
            )
            self._db.executemany(
                "INSERT INTO operations VALUES (?, ?, ?, ?, 'pending', '{}', ?)",
                [(plan_id, index, vid, name, now) for index, (vid, name) in enumerate(normalized)],
            )
        return self.get_plan(plan_id)

    def get_plan(self, plan_id: str) -> dict:
        plan_id = _identifier(plan_id, "plan_id")
        with self._lock:
            plan = self._db.execute("SELECT * FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
            if plan is None:
                raise ValueError("plan not found")
            rows = self._db.execute(
                "SELECT * FROM operations WHERE plan_id=? ORDER BY operation_index", (plan_id,)
            ).fetchall()
            assignments = [
                {
                    "index": row["operation_index"],
                    "video_id": row["video_id"],
                    "folder_name": row["folder_name"],
                    "status": row["status"],
                    "detail": json.loads(row["detail_json"]),
                    "updated_at": row["updated_at"],
                }
                for row in rows
            ]
            statuses = {row["status"] for row in rows}
            # Ambiguous writes require review even if other operations are in progress.
            status = next(
                (s for s in ("uncertain", "running", "failed", "pending") if s in statuses),
                "completed",
            )
            return {
                "plan_id": plan["plan_id"],
                "account_id": plan["account_id"],
                "mode": plan["mode"],
                "created_at": plan["created_at"],
                "status": status,
                "assignments": assignments,
            }

    def set_operation(self, plan_id: str, index: int, status: str, detail: dict) -> None:
        plan_id = _identifier(plan_id, "plan_id")
        if type(index) is not int or not 0 <= index < 200:
            raise ValueError("operation index must be an integer between 0 and 199")
        if not isinstance(status, str) or status not in OPERATION_STATUSES:
            raise ValueError("invalid operation status")
        serialized = _json(detail, "detail")
        with self._lock, self._db:
            result = self._db.execute(
                "UPDATE operations SET status=?, detail_json=?, updated_at=? "
                "WHERE plan_id=? AND operation_index=?",
                (status, serialized, _now(), plan_id, index),
            )
            if result.rowcount != 1:
                raise ValueError("plan operation not found")

    def close(self) -> None:
        with self._lock:
            self._db.close()
