"""OpenCode SQLite storage provider.

This module implements storage compatible with OpenCode's SQLite database format
(>= 1.2). The database is typically located at ~/.local/share/opencode/opencode.db.

Schema overview:
- project: id, worktree, vcs, name, ...
- session: id, project_id, parent_id, slug, directory, title, version, ...
- message: id, session_id, time_created, time_updated, data (JSON)
- part: id, message_id, session_id, time_created, time_updated, data (JSON)
- todo: session_id, content, status, priority, position, ...

Message and part data is stored as JSON text columns. The 'data' field contains
the full message/part payload minus the id and session_id which are separate columns.

Timestamps are stored as integer milliseconds since epoch.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING, Any

import anyenv

from agentpool.log import get_logger
from agentpool.utils.time_utils import get_now, ms_to_datetime
from agentpool_config.storage import OpenCodeStorageConfig
from agentpool_storage.base import StorageProvider
from agentpool_storage.models import ConversationData as ConvData, TokenUsage
from agentpool_storage.opencode_provider import helpers


if TYPE_CHECKING:
    from datetime import datetime

    from agentpool.messaging import ChatMessage
    from agentpool_config.session import SessionQuery
    from agentpool_storage.models import QueryFilters, StatsFilters

logger = get_logger(__name__)


class OpenCodeStorageProvider(StorageProvider):
    """Storage provider that reads OpenCode's native SQLite format.

    OpenCode (>= 1.2) stores data in a single SQLite database:
    - ~/.local/share/opencode/opencode.db

    Tables:
    - project: project/worktree metadata
    - session: conversation sessions linked to projects
    - message: messages with JSON data column
    - part: message parts with JSON data column

    This is primarily a READ-ONLY provider for importing OpenCode history.
    """

    can_load_history = True

    def __init__(self, config: OpenCodeStorageConfig | None = None) -> None:
        """Initialize OpenCode SQLite storage provider."""
        config = config or OpenCodeStorageConfig()
        super().__init__(config)
        self.db_path = Path(config.path).expanduser()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a SQLite connection with row factory."""
        if not self.db_path.exists():
            msg = f"OpenCode database not found: {self.db_path}"
            raise FileNotFoundError(msg)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _read_messages(self, session_id: str) -> list[sqlite3.Row]:
        """Read all messages for a session, ordered by time_created."""
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

    def _read_parts_for_message(self, message_id: str) -> list[sqlite3.Row]:
        """Read all parts for a message, ordered by id."""
        try:
            conn = self._get_connection()
        except FileNotFoundError:
            return []
        try:
            cursor = conn.execute(
                "SELECT id, message_id, session_id, time_created, time_updated, data "
                "FROM part WHERE message_id = ? ORDER BY id ASC",
                (message_id,),
            )
            return cursor.fetchall()
        finally:
            conn.close()

    def _read_parts_for_session(self, session_id: str) -> dict[str, list[dict[str, Any]]]:
        """Read all parts for a session, grouped by message_id.

        Returns:
            Dict mapping message_id -> list of part data dicts
        """
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
            result: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in cursor:
                data = anyenv.load_json(row["data"])
                result[row["message_id"]].append(data)
            return result
        finally:
            conn.close()

    def _parse_msg_data(self, row: sqlite3.Row) -> dict[str, Any]:
        """Parse the JSON data column from a message row."""
        return anyenv.load_json(row["data"])

    def _parse_part_data(self, row: sqlite3.Row) -> dict[str, Any]:
        """Parse the JSON data column from a part row."""
        return anyenv.load_json(row["data"])

    async def filter_messages(self, query: SessionQuery) -> list[ChatMessage[str]]:
        """Filter messages based on query."""
        from datetime import datetime

        messages: list[ChatMessage[str]] = []
        try:
            conn = self._get_connection()
        except FileNotFoundError:
            return []
        try:
            # Build session query
            if query.name:
                session_rows = conn.execute(
                    "SELECT id FROM session WHERE id = ?", (query.name,)
                ).fetchall()
            else:
                session_rows = conn.execute("SELECT id FROM session").fetchall()

            for session_row in session_rows:
                session_id: str = session_row["id"]
                msg_rows = conn.execute(
                    "SELECT id, session_id, time_created, time_updated, data "
                    "FROM message WHERE session_id = ? ORDER BY time_created ASC",
                    (session_id,),
                ).fetchall()

                parts_by_msg = self._read_parts_for_session(session_id)

                for msg_row in msg_rows:
                    msg_id: str = msg_row["id"]
                    msg_data = anyenv.load_json(msg_row["data"])
                    parts = parts_by_msg.get(msg_id, [])

                    chat_msg = helpers.to_chat_message(
                        message_id=msg_id,
                        session_id=session_id,
                        msg_data=msg_data,
                        parts=parts,
                        time_created=msg_row["time_created"],
                    )

                    # Apply filters
                    if query.agents and chat_msg.name not in query.agents:
                        continue
                    cutoff = query.get_time_cutoff()
                    if query.since and cutoff and chat_msg.timestamp < cutoff:
                        continue
                    if query.until:
                        until_dt = datetime.fromisoformat(query.until)
                        if chat_msg.timestamp > until_dt:
                            continue
                    if query.contains and query.contains not in chat_msg.content:
                        continue
                    if query.roles and chat_msg.role not in query.roles:
                        continue
                    messages.append(chat_msg)

                    if query.limit and len(messages) >= query.limit:
                        return messages
        finally:
            conn.close()

        return messages

    async def log_message(self, *, message: ChatMessage[Any]) -> None:
        """Log a message - not supported for read-only provider."""
        logger.debug("log_message not implemented for OpenCode SQLite provider (read-only)")

    async def log_session(
        self,
        *,
        session_id: str,
        node_name: str,
        start_time: datetime | None = None,
        model: str | None = None,
    ) -> None:
        """Log a conversation start - not supported for read-only provider."""

    async def get_sessions(self, filters: QueryFilters) -> list[ConvData]:
        """Get filtered conversations with their messages."""
        result: list[ConvData] = []
        try:
            conn = self._get_connection()
        except FileNotFoundError:
            return []
        try:
            # Build SQL conditions
            conditions: list[str] = []
            params: list[Any] = []

            if filters.since:
                since_ms = int(filters.since.timestamp() * 1000)
                conditions.append("s.time_created >= ?")
                params.append(since_ms)

            where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
            sql = (
                f"SELECT s.id, s.title, s.time_created, s.time_updated, s.project_id "
                f"FROM session s{where} ORDER BY s.time_updated DESC"
            )
            if filters.limit:
                sql += " LIMIT ?"
                params.append(filters.limit * 2)  # Over-fetch since we filter more below

            session_rows = conn.execute(sql, params).fetchall()

            for session_row in session_rows:
                session_id: str = session_row["id"]
                title: str = session_row["title"]
                time_created: int = session_row["time_created"]

                # Read messages for this session
                msg_rows = conn.execute(
                    "SELECT id, session_id, time_created, time_updated, data "
                    "FROM message WHERE session_id = ? ORDER BY time_created ASC",
                    (session_id,),
                ).fetchall()

                if not msg_rows:
                    continue

                parts_by_msg = self._read_parts_for_session(session_id)

                chat_messages: list[ChatMessage[str]] = []
                total_tokens = 0

                for msg_row in msg_rows:
                    msg_id: str = msg_row["id"]
                    msg_data = anyenv.load_json(msg_row["data"])
                    parts = parts_by_msg.get(msg_id, [])

                    chat_msg = helpers.to_chat_message(
                        message_id=msg_id,
                        session_id=session_id,
                        msg_data=msg_data,
                        parts=parts,
                        time_created=msg_row["time_created"],
                    )
                    chat_messages.append(chat_msg)

                    # Count tokens from assistant messages
                    if msg_data.get("role") == "assistant":
                        tokens = msg_data.get("tokens", {})
                        total_tokens += tokens.get("input", 0) + tokens.get("output", 0)

                if not chat_messages:
                    continue

                first_timestamp = ms_to_datetime(time_created)

                # Apply remaining filters
                if filters.agent_name and not any(
                    m.name == filters.agent_name for m in chat_messages
                ):
                    continue
                if filters.query and not any(filters.query in m.content for m in chat_messages):
                    continue

                usage = (
                    TokenUsage(total=total_tokens, prompt=0, completion=0) if total_tokens else None
                )
                conv_data = ConvData(
                    id=session_id,
                    agent=chat_messages[0].name or "opencode",
                    title=title,
                    start_time=first_timestamp.isoformat(),
                    messages=chat_messages,
                    token_usage=usage,
                )
                result.append(conv_data)
                if filters.limit and len(result) >= filters.limit:
                    break
        finally:
            conn.close()

        return result

    async def get_session_stats(self, filters: StatsFilters) -> dict[str, dict[str, Any]]:
        """Get conversation statistics."""
        stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "total_tokens": 0,
                "messages": 0,
                "models": set(),
                "total_cost": 0.0,
            }
        )
        try:
            conn = self._get_connection()
        except FileNotFoundError:
            return {}
        try:
            cutoff_ms = int(filters.cutoff.timestamp() * 1000)

            # Query messages with their data, filtered by time
            cursor = conn.execute(
                "SELECT m.id, m.time_created, m.data "
                "FROM message m "
                "JOIN session s ON m.session_id = s.id "
                "WHERE s.time_created >= ?",
                (cutoff_ms,),
            )

            for row in cursor:
                msg_data = anyenv.load_json(row["data"])
                role = msg_data.get("role", "")
                if role != "assistant":
                    continue

                model = msg_data.get("modelID", "unknown")
                tokens_data = msg_data.get("tokens", {})
                tokens = tokens_data.get("input", 0) + tokens_data.get("output", 0)
                cost = msg_data.get("cost", 0.0)

                msg_timestamp = ms_to_datetime(row["time_created"])

                match filters.group_by:
                    case "model":
                        key = model
                    case "hour":
                        key = msg_timestamp.strftime("%Y-%m-%d %H:00")
                    case "day":
                        key = msg_timestamp.strftime("%Y-%m-%d")
                    case _:
                        key = msg_data.get("agent", "opencode") or "opencode"

                stats[key]["messages"] += 1
                stats[key]["total_tokens"] += tokens
                stats[key]["models"].add(model)
                stats[key]["total_cost"] += cost or 0.0
        finally:
            conn.close()

        # Convert sets to lists
        for value in stats.values():
            value["models"] = list(value["models"])

        return dict(stats)

    async def reset(self, *, agent_name: str | None = None, hard: bool = False) -> tuple[int, int]:
        """Reset storage - not supported for read-only provider."""
        logger.warning("Reset not implemented for OpenCode SQLite storage (read-only)")
        return 0, 0

    async def get_session_counts(self, *, agent_name: str | None = None) -> tuple[int, int]:
        """Get counts of conversations and messages."""
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

    async def get_session_messages(
        self,
        session_id: str,
        *,
        include_ancestors: bool = False,
    ) -> list[ChatMessage[str]]:
        """Get all messages for a session."""
        messages: list[ChatMessage[str]] = []
        msg_rows = self._read_messages(session_id)
        parts_by_msg = self._read_parts_for_session(session_id)

        for msg_row in msg_rows:
            msg_id: str = msg_row["id"]
            msg_data = anyenv.load_json(msg_row["data"])
            parts = parts_by_msg.get(msg_id, [])

            chat_msg = helpers.to_chat_message(
                message_id=msg_id,
                session_id=session_id,
                msg_data=msg_data,
                parts=parts,
                time_created=msg_row["time_created"],
            )
            messages.append(chat_msg)

        # Sort by timestamp
        now = get_now()
        messages.sort(key=lambda m: m.timestamp or now)

        if not include_ancestors or not messages:
            return messages

        # Get ancestor chain if first message has parent_id
        if parent_id := messages[0].parent_id:
            ancestors = await self.get_message_ancestry(parent_id, session_id=session_id)
            return ancestors + messages
        return messages

    async def get_message(
        self,
        message_id: str,
        *,
        session_id: str | None = None,
    ) -> ChatMessage[str] | None:
        """Get a single message by ID."""
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

            sid: str = row["session_id"]
            msg_data = anyenv.load_json(row["data"])

            # Read parts for this message
            part_rows = conn.execute(
                "SELECT data FROM part WHERE message_id = ? ORDER BY id ASC",
                (message_id,),
            ).fetchall()
            parts = [anyenv.load_json(p["data"]) for p in part_rows]

            return helpers.to_chat_message(
                message_id=message_id,
                session_id=sid,
                msg_data=msg_data,
                parts=parts,
                time_created=row["time_created"],
            )
        finally:
            conn.close()

    async def get_message_ancestry(
        self,
        message_id: str,
        *,
        session_id: str | None = None,
    ) -> list[ChatMessage[str]]:
        """Get the ancestry chain of a message.

        Traverses parent_id chain to build full history.

        Args:
            message_id: ID of the message
            session_id: Optional session ID hint for faster lookup

        Returns:
            List of messages from oldest ancestor to the specified message
        """
        ancestors: list[ChatMessage[str]] = []

        if session_id:
            # Fast path: load all messages for session and traverse in-memory
            msg_rows = self._read_messages(session_id)
            parts_by_msg = self._read_parts_for_session(session_id)

            msg_by_id: dict[str, tuple[sqlite3.Row, list[dict[str, Any]]]] = {}
            for msg_row in msg_rows:
                mid: str = msg_row["id"]
                msg_by_id[mid] = (msg_row, parts_by_msg.get(mid, []))

            current_id: str | None = message_id
            while current_id:
                entry = msg_by_id.get(current_id)
                if not entry:
                    break
                msg_row, parts = entry
                msg_data = anyenv.load_json(msg_row["data"])
                chat_msg = helpers.to_chat_message(
                    message_id=current_id,
                    session_id=session_id,
                    msg_data=msg_data,
                    parts=parts,
                    time_created=msg_row["time_created"],
                )
                ancestors.append(chat_msg)
                current_id = chat_msg.parent_id
            ancestors.reverse()
            return ancestors

        # Slow path: search by message ID
        current_id = message_id
        while current_id:
            msg = await self.get_message(current_id)
            if not msg:
                break
            ancestors.append(msg)
            current_id = msg.parent_id
        ancestors.reverse()
        return ancestors

    async def fork_conversation(
        self,
        *,
        source_session_id: str,
        new_session_id: str,
        fork_from_message_id: str | None = None,
        new_agent_name: str | None = None,
    ) -> str | None:
        """Fork a conversation - not supported for read-only provider."""
        logger.warning("fork_conversation not implemented for OpenCode SQLite storage (read-only)")
        msg = "OpenCodeStorageProvider (SQLite) does not support forking (read-only)"
        raise NotImplementedError(msg)

    async def get_session_title(self, session_id: str) -> str | None:
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


if __name__ == "__main__":
    import asyncio
    import datetime as dt

    from agentpool_storage.models import QueryFilters, StatsFilters

    async def main() -> None:
        provider = OpenCodeStorageProvider()
        print(f"Database path: {provider.db_path}")
        print(f"Exists: {provider.db_path.exists()}")

        # Get counts
        conv_count, msg_count = await provider.get_session_counts()
        print(f"\nTotal: {conv_count} sessions, {msg_count} messages")

        # List conversations
        filters = QueryFilters(limit=10)
        conversations = await provider.get_sessions(filters)
        print(f"\nFound {len(conversations)} conversations")
        for conv_data in conversations[:5]:
            print(f"  - {conv_data['id'][:8]}... | {conv_data['title'] or 'Untitled'}")
            print(f"    Messages: {len(conv_data['messages'])}, Updated: {conv_data['start_time']}")

        # Get stats
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)
        stats_filters = StatsFilters(cutoff=cutoff, group_by="day")
        stats = await provider.get_session_stats(stats_filters)
        print(f"\nStats: {stats}")

    asyncio.run(main())
