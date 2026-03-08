"""Read-only client for OpenCode's SQLite database.

Provides access to OpenCode's native SQLite format (>= 1.2),
returning only OpenCode SDK models (Session, MessageWithParts, etc.).

The database is typically located at ~/.local/share/opencode/opencode.db.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING, Any

import anyenv

from agentpool.log import get_logger
from opencode_sdk.helpers import parse_message_info, parse_part
from opencode_sdk.models.message import MessageWithParts
from opencode_sdk.models.session import Session


if TYPE_CHECKING:
    from opencode_sdk.models.message import MessageInfo
    from opencode_sdk.models.parts import Part


logger = get_logger(__name__)

DEFAULT_DB_PATH = "~/.local/share/opencode/opencode.db"


class OpenCodeStorageClient:
    """Read-only client for OpenCode's SQLite database.

    All methods return OpenCode SDK models — no agentpool-specific types.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path).expanduser()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a SQLite connection with row factory."""
        if not self.db_path.exists():
            raise FileNotFoundError(f"OpenCode database not found: {self.db_path}")
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ── Sessions ──────────────────────────────────────────────────────

    def get_session(self, session_id: str) -> Session | None:
        """Get a single session by ID."""
        try:
            conn = self._get_connection()
        except FileNotFoundError:
            return None
        try:
            row = conn.execute(
                "SELECT id, project_id, parent_id, directory, title, version, "
                "time_created, time_updated FROM session WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                return None
            return self._parse_session_row(row)
        finally:
            conn.close()

    def get_sessions(
        self,
        *,
        since_ms: int | None = None,
        limit: int | None = None,
    ) -> list[Session]:
        """Get sessions, optionally filtered by time and limited."""
        try:
            conn = self._get_connection()
        except FileNotFoundError:
            return []
        try:
            conditions: list[str] = []
            params: list[Any] = []
            if since_ms is not None:
                conditions.append("time_created >= ?")
                params.append(since_ms)

            where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
            sql = (
                "SELECT id, project_id, parent_id, directory, title, version, "
                f"time_created, time_updated FROM session{where} "
                "ORDER BY time_updated DESC"
            )
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)

            return [self._parse_session_row(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def get_session_title(self, session_id: str) -> str | None:
        """Get the title of a session."""
        try:
            conn = self._get_connection()
        except FileNotFoundError:
            return None
        try:
            row = conn.execute(
                "SELECT title FROM session WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row:
                title: str = row["title"]
                return title
            return None
        finally:
            conn.close()

    def get_session_ids(self, name: str | None = None) -> list[str]:
        """Get session IDs, optionally filtered by exact ID match."""
        try:
            conn = self._get_connection()
        except FileNotFoundError:
            return []
        try:
            if name:
                rows = conn.execute("SELECT id FROM session WHERE id = ?", (name,)).fetchall()
            else:
                rows = conn.execute("SELECT id FROM session").fetchall()
            return [row["id"] for row in rows]
        finally:
            conn.close()

    def get_session_counts(self) -> tuple[int, int]:
        """Get total count of sessions and messages."""
        try:
            conn = self._get_connection()
        except FileNotFoundError:
            return 0, 0
        try:
            session_count: int = conn.execute("SELECT COUNT(*) FROM session").fetchone()[0]
            msg_count: int = conn.execute("SELECT COUNT(*) FROM message").fetchone()[0]
            return session_count, msg_count
        finally:
            conn.close()

    # ── Messages ──────────────────────────────────────────────────────

    def get_session_messages(self, session_id: str) -> list[MessageWithParts]:
        """Get all messages with their parts for a session, ordered by time."""
        msg_rows = self._read_message_rows(session_id)
        if not msg_rows:
            return []
        parts_by_msg = self._read_parts_for_session(session_id)
        result: list[MessageWithParts] = []
        for row in msg_rows:
            msg_id: str = row["id"]
            info = self._parse_message_row(row)
            parts = parts_by_msg.get(msg_id, [])
            result.append(MessageWithParts(info=info, parts=parts))
        return result

    def get_message(self, message_id: str) -> MessageWithParts | None:
        """Get a single message with its parts by message ID."""
        try:
            conn = self._get_connection()
        except FileNotFoundError:
            return None
        try:
            row = conn.execute(
                "SELECT id, session_id, time_created, time_updated, data FROM message WHERE id = ?",
                (message_id,),
            ).fetchone()
            if not row:
                return None
            info = self._parse_message_row(row)
            parts = self._read_parts_for_message(message_id)
            return MessageWithParts(info=info, parts=parts)
        finally:
            conn.close()

    def get_messages_with_data(
        self,
        *,
        since_ms: int | None = None,
    ) -> list[MessageWithParts]:
        """Get messages (with parts) across all sessions, optionally filtered by time.

        Used for stats queries that need to iterate all messages.
        """
        try:
            conn = self._get_connection()
        except FileNotFoundError:
            return []
        try:
            if since_ms is not None:
                cursor = conn.execute(
                    "SELECT m.id, m.session_id, m.time_created, m.time_updated, m.data "
                    "FROM message m "
                    "JOIN session s ON m.session_id = s.id "
                    "WHERE s.time_created >= ?",
                    (since_ms,),
                )
            else:
                cursor = conn.execute(
                    "SELECT id, session_id, time_created, time_updated, data FROM message"
                )
            results: list[MessageWithParts] = []
            for row in cursor:
                info = self._parse_message_row(row)
                # No parts loaded here — caller can load parts if needed
                results.append(MessageWithParts(info=info))
            return results
        finally:
            conn.close()

    # ── Internal helpers ──────────────────────────────────────────────

    @staticmethod
    def _parse_session_row(row: sqlite3.Row) -> Session:
        """Parse a session DB row into a Session model."""
        from opencode_sdk.models.common import TimeCreatedUpdated

        return Session(
            id=row["id"],
            project_id=row["project_id"],
            parent_id=row["parent_id"],
            directory=row["directory"],
            title=row["title"],
            version=row["version"] if "version" in row else "1",  # noqa: SIM401
            time=TimeCreatedUpdated(
                created=row["time_created"],
                updated=row["time_updated"],
            ),
        )

    def _read_message_rows(self, session_id: str) -> list[sqlite3.Row]:
        """Read all message rows for a session, ordered by time_created."""
        try:
            conn = self._get_connection()
        except FileNotFoundError:
            return []
        try:
            cursor = conn.execute(
                "SELECT id, session_id, time_created, time_updated, data "
                "FROM message WHERE session_id = ? ORDER BY time_created ASC",
                (session_id,),
            )
            return cursor.fetchall()
        finally:
            conn.close()

    @staticmethod
    def _parse_message_row(row: sqlite3.Row) -> MessageInfo:
        """Parse a message DB row into a MessageInfo model."""
        data: dict[str, Any] = anyenv.load_json(row["data"], return_type=dict)
        return parse_message_info(data, message_id=row["id"], session_id=row["session_id"])

    def _read_parts_for_session(self, session_id: str) -> dict[str, list[Part]]:
        """Read all parts for a session, grouped by message_id."""
        try:
            conn = self._get_connection()
        except FileNotFoundError:
            return {}
        try:
            cursor = conn.execute(
                "SELECT id, message_id, session_id, data "
                "FROM part WHERE session_id = ? ORDER BY message_id, id ASC",
                (session_id,),
            )
            result: dict[str, list[Part]] = defaultdict(list)
            for row in cursor:
                data: dict[str, Any] = anyenv.load_json(row["data"], return_type=dict)
                try:
                    part = parse_part(
                        data,
                        part_id=row["id"],
                        message_id=row["message_id"],
                        session_id=row["session_id"],
                    )
                    result[row["message_id"]].append(part)
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "Failed to parse part, skipping",
                        part_id=row["id"],
                        part_type=data.get("type", "unknown"),
                    )
            return result
        finally:
            conn.close()

    def _read_parts_for_message(self, message_id: str) -> list[Part]:
        """Read all parts for a single message, ordered by id."""
        try:
            conn = self._get_connection()
        except FileNotFoundError:
            return []
        try:
            cursor = conn.execute(
                "SELECT id, message_id, session_id, data "
                "FROM part WHERE message_id = ? ORDER BY id ASC",
                (message_id,),
            )
            parts: list[Part] = []
            for row in cursor:
                data: dict[str, Any] = anyenv.load_json(row["data"], return_type=dict)
                try:
                    part = parse_part(
                        data,
                        part_id=row["id"],
                        message_id=row["message_id"],
                        session_id=row["session_id"],
                    )
                    parts.append(part)
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "Failed to parse part, skipping",
                        part_id=row["id"],
                        part_type=data.get("type", "unknown"),
                    )
            return parts
        finally:
            conn.close()
