"""OpenCode SQLite storage provider.

This module implements storage compatible with OpenCode's SQLite database format
(>= 1.2). The database is typically located at ~/.local/share/opencode/opencode.db.

This provider delegates all SQLite access to OpenCodeStorageClient and converts
the OpenCode SDK models to agentpool ChatMessage / ConversationData types.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

from agentpool.log import get_logger
from agentpool.utils.time_utils import datetime_to_ms, get_now, ms_to_datetime
from agentpool_config.storage import OpenCodeStorageConfig
from agentpool_storage.base import StorageProvider
from agentpool_storage.models import ConversationData as ConvData, TokenUsage
from agentpool_storage.opencode_provider import helpers
from opencode_sdk.models.message import AssistantMessage
from opencode_sdk.storage_client import OpenCodeStorageClient


if TYPE_CHECKING:
    from agentpool.messaging import ChatMessage
    from agentpool_config.session import SessionQuery
    from agentpool_storage.models import QueryFilters, StatsFilters
    from opencode_sdk.models.message import MessageWithParts

logger = get_logger(__name__)


def _to_chat_message(mwp: MessageWithParts) -> ChatMessage[str]:
    """Convert a MessageWithParts to a ChatMessage."""
    return helpers.to_chat_message(msg=mwp.info, parts=mwp.parts)


class OpenCodeStorageProvider(StorageProvider):
    """Storage provider that reads OpenCode's native SQLite format.

    This is primarily a READ-ONLY provider for importing OpenCode history.
    All SQLite access is delegated to OpenCodeStorageClient; this class
    only converts between OpenCode models and agentpool types.
    """

    can_load_history = True

    def __init__(self, config: OpenCodeStorageConfig | None = None) -> None:
        """Initialize OpenCode SQLite storage provider."""
        config = config or OpenCodeStorageConfig()
        super().__init__(config)
        self.client = OpenCodeStorageClient(db_path=config.path)

    async def filter_messages(self, query: SessionQuery) -> list[ChatMessage[str]]:
        """Filter messages based on query."""
        messages: list[ChatMessage[str]] = []
        session_ids = self.client.get_session_ids(name=query.name)

        for session_id in session_ids:
            session_msgs = self.client.get_session_messages(session_id)
            for mwp in session_msgs:
                chat_msg = _to_chat_message(mwp)
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
        agent_type: str | None = None,
    ) -> None:
        """Log a conversation start - not supported for read-only provider."""

    async def get_sessions(self, filters: QueryFilters) -> list[ConvData]:
        """Get filtered conversations with their messages."""
        result: list[ConvData] = []
        since_ms = datetime_to_ms(filters.since) if filters.since else None
        # Over-fetch since we filter more below
        limit = filters.limit * 2 if filters.limit else None
        sessions = self.client.get_sessions(since_ms=since_ms, limit=limit)

        for session in sessions:
            session_msgs = self.client.get_session_messages(session.id)
            if not session_msgs:
                continue

            chat_messages: list[ChatMessage[str]] = []
            total_tokens = 0
            for mwp in session_msgs:
                chat_msg = _to_chat_message(mwp)
                chat_messages.append(chat_msg)
                if isinstance(mwp.info, AssistantMessage):
                    total_tokens += mwp.info.tokens.input + mwp.info.tokens.output

            if not chat_messages:
                continue

            # Apply remaining filters
            if filters.agent_name and not any(m.name == filters.agent_name for m in chat_messages):
                continue
            if filters.query and not any(filters.query in m.content for m in chat_messages):
                continue

            usage = TokenUsage(total=total_tokens, prompt=0, completion=0) if total_tokens else None
            conv_data = ConvData(
                id=session.id,
                agent=chat_messages[0].name or "opencode",
                title=session.title,
                start_time=ms_to_datetime(session.time.created).isoformat(),
                messages=chat_messages,
                token_usage=usage,
            )
            result.append(conv_data)
            if filters.limit and len(result) >= filters.limit:
                break

        return result

    async def get_session_stats(self, filters: StatsFilters) -> dict[str, dict[str, Any]]:
        """Get conversation statistics."""
        stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"total_tokens": 0, "messages": 0, "models": set(), "total_cost": 0.0}
        )
        cutoff_ms = datetime_to_ms(filters.cutoff)
        messages_with_data = self.client.get_messages_with_data(since_ms=cutoff_ms)

        for mwp in messages_with_data:
            msg = mwp.info
            if not isinstance(msg, AssistantMessage):
                continue

            tokens = msg.tokens.input + msg.tokens.output
            msg_timestamp = ms_to_datetime(msg.time.created)

            match filters.group_by:
                case "model":
                    key = msg.model_id
                case "hour":
                    key = msg_timestamp.strftime("%Y-%m-%d %H:00")
                case "day":
                    key = msg_timestamp.strftime("%Y-%m-%d")
                case _:
                    key = msg.agent if msg.agent != "default" else "opencode"

            stats[key]["messages"] += 1
            stats[key]["total_tokens"] += tokens
            stats[key]["models"].add(msg.model_id)
            stats[key]["total_cost"] += msg.cost

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
        return self.client.get_session_counts()

    async def get_session_messages(
        self,
        session_id: str,
        *,
        include_ancestors: bool = False,
    ) -> list[ChatMessage[str]]:
        """Get all messages for a session."""
        session_msgs = self.client.get_session_messages(session_id)
        messages = [_to_chat_message(mwp) for mwp in session_msgs]

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
        mwp = self.client.get_message(message_id)
        if mwp is None:
            return None
        return _to_chat_message(mwp)

    async def get_message_ancestry(
        self,
        message_id: str,
        *,
        session_id: str | None = None,
    ) -> list[ChatMessage[str]]:
        """Get the ancestry chain of a message.

        Traverses parent_id chain to build full history.
        """
        ancestors: list[ChatMessage[str]] = []

        if session_id:
            # Fast path: load all messages for session and traverse in-memory
            session_msgs = self.client.get_session_messages(session_id)
            msg_by_id: dict[str, MessageWithParts] = {mwp.info.id: mwp for mwp in session_msgs}

            current_id: str | None = message_id
            while current_id:
                mwp = msg_by_id.get(current_id)
                if mwp is None:
                    break
                chat_msg = _to_chat_message(mwp)
                ancestors.append(chat_msg)
                current_id = chat_msg.parent_id
            ancestors.reverse()
            return ancestors

        # Slow path: fetch one message at a time
        current_id = message_id
        while current_id:
            ancestor_msg = await self.get_message(current_id)
            if not ancestor_msg:
                break
            ancestors.append(ancestor_msg)
            current_id = ancestor_msg.parent_id
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
        return self.client.get_session_title(session_id)


if __name__ == "__main__":
    import asyncio
    import datetime as dt

    from agentpool_storage.models import QueryFilters, StatsFilters

    async def main() -> None:
        provider = OpenCodeStorageProvider()
        print(f"Database path: {provider.client.db_path}")
        print(f"Exists: {provider.client.db_path.exists()}")

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
