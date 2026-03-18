"""Broadcast tests for SkillCommandRegistry change notifications."""

from __future__ import annotations

from typing import Any

import pytest
from upathtools import UPath

from agentpool.skills.command import SkillCommand
from agentpool.skills.command_registry import CommandChangeHandler, SkillCommandRegistry
from agentpool.skills.skill import Skill


def create_test_skill(name: str = "test-skill", description: str = "A test skill") -> Skill:
    """Create a minimal test skill."""
    return Skill(
        name=name,
        description=description,
        skill_path=UPath("/tmp/test-skill"),
    )


def create_test_command(
    name: str = "test-command", description: str = "A test command"
) -> SkillCommand:
    """Create a minimal test command."""
    skill = create_test_skill(name, description)
    return SkillCommand(
        name=name,
        description=description,
        skill=skill,
    )


class TestOnCommandChangeRegistration:
    """Tests for on_command_change callback registration."""

    def test_handler_receives_add_notification(self) -> None:
        """Test that handler receives notification when command is added."""
        registry = SkillCommandRegistry()
        events: list[tuple[str, SkillCommand | None]] = []

        def handler(name: str, command: SkillCommand | None) -> None:
            events.append((name, command))

        registry.on_command_change(handler)
        command = create_test_command("new-cmd")

        registry.register("new-cmd", command)

        assert len(events) == 1
        assert events[0][0] == "new-cmd"
        assert events[0][1] is command

    def test_handler_receives_remove_notification(self) -> None:
        """Test that handler receives notification with None when command is removed."""
        registry = SkillCommandRegistry()
        events: list[tuple[str, SkillCommand | None]] = []

        def handler(name: str, command: SkillCommand | None) -> None:
            events.append((name, command))

        command = create_test_command("to-remove")
        registry.register("to-remove", command)

        registry.on_command_change(handler)
        del registry["to-remove"]

        # Should have 1 event for the existing command, then 1 for removal
        assert len(events) == 2
        assert events[1][0] == "to-remove"
        assert events[1][1] is None

    def test_handler_receives_notification_on_dict_style_assignment(self) -> None:
        """Test that handler receives notification on dict-style assignment."""
        registry = SkillCommandRegistry()
        events: list[tuple[str, SkillCommand | None]] = []

        def handler(name: str, command: SkillCommand | None) -> None:
            events.append((name, command))

        registry.on_command_change(handler)
        command = create_test_command("dict-cmd")

        registry["dict-cmd"] = command

        assert len(events) == 1
        assert events[0][0] == "dict-cmd"
        assert events[0][1] is command


class TestMultipleHandlers:
    """Tests for multiple handler support."""

    def test_multiple_handlers_receive_notifications(self) -> None:
        """Test that multiple handlers all receive change notifications."""
        registry = SkillCommandRegistry()
        events1: list[tuple[str, SkillCommand | None]] = []
        events2: list[tuple[str, SkillCommand | None]] = []

        def handler1(name: str, command: SkillCommand | None) -> None:
            events1.append((name, command))

        def handler2(name: str, command: SkillCommand | None) -> None:
            events2.append((name, command))

        registry.on_command_change(handler1)
        registry.on_command_change(handler2)

        command = create_test_command("multi-cmd")
        registry.register("multi-cmd", command)

        assert len(events1) == 1
        assert len(events2) == 1
        assert events1[0][0] == "multi-cmd"
        assert events2[0][0] == "multi-cmd"

    def test_handlers_receive_independent_notifications(self) -> None:
        """Test that handlers maintain independent event lists."""
        registry = SkillCommandRegistry()
        events1: list[str] = []
        events2: list[str] = []

        def handler1(name: str, command: SkillCommand | None) -> None:
            events1.append(f"handler1: {name}")

        def handler2(name: str, command: SkillCommand | None) -> None:
            events2.append(f"handler2: {name}")

        registry.on_command_change(handler1)
        registry.on_command_change(handler2)

        command = create_test_command("test-cmd")
        registry.register("test-cmd", command)

        assert events1 == ["handler1: test-cmd"]
        assert events2 == ["handler2: test-cmd"]


class TestExistingCommandsNotification:
    """Tests for notifying new handlers of existing commands."""

    def test_new_handler_notified_of_existing_commands(self) -> None:
        """Test that new handler is immediately notified of existing commands."""
        registry = SkillCommandRegistry()
        command1 = create_test_command("cmd1")
        command2 = create_test_command("cmd2")

        registry.register("cmd1", command1)
        registry.register("cmd2", command2)

        events: list[tuple[str, SkillCommand | None]] = []

        def handler(name: str, command: SkillCommand | None) -> None:
            events.append((name, command))

        registry.on_command_change(handler)

        # Should be notified of both existing commands
        assert len(events) == 2
        names = {e[0] for e in events}
        assert names == {"cmd1", "cmd2"}

    def test_existing_commands_notification_on_late_subscription(self) -> None:
        """Test that late subscriber gets notified of all existing commands."""
        registry = SkillCommandRegistry()

        # Register commands before subscribing
        for i in range(3):
            cmd = create_test_command(f"cmd-{i}")
            registry.register(f"cmd-{i}", cmd)

        # Late subscription
        events: list[str] = []

        def handler(name: str, command: SkillCommand | None) -> None:
            events.append(name)

        registry.on_command_change(handler)

        assert len(events) == 3
        assert "cmd-0" in events
        assert "cmd-1" in events
        assert "cmd-2" in events

    def test_new_handler_empty_registry(self) -> None:
        """Test that new handler on empty registry receives no notifications."""
        registry = SkillCommandRegistry()
        events: list[tuple[str, SkillCommand | None]] = []

        def handler(name: str, command: SkillCommand | None) -> None:
            events.append((name, command))

        registry.on_command_change(handler)

        assert len(events) == 0


class TestHandlerNotCalledWithoutChanges:
    """Tests that handlers are not called when no changes occur."""

    def test_handler_not_called_when_no_changes(self) -> None:
        """Test that handler is not called without register or delete operations."""
        registry = SkillCommandRegistry()
        events: list[tuple[str, SkillCommand | None]] = []

        def handler(name: str, command: SkillCommand | None) -> None:
            events.append((name, command))

        registry.on_command_change(handler)

        # No operations performed
        assert len(events) == 0

    def test_handler_not_called_on_get_operation(self) -> None:
        """Test that handler is not called on get operations."""
        registry = SkillCommandRegistry()
        command = create_test_command("get-cmd")
        registry.register("get-cmd", command)

        events: list[tuple[str, SkillCommand | None]] = []

        def handler(name: str, command: SkillCommand | None) -> None:
            events.append((name, command))

        registry.on_command_change(handler)

        # Clear events from initial notification
        events.clear()

        # Perform get operation
        _ = registry.get("get-cmd")
        _ = registry["get-cmd"]

        assert len(events) == 0

    def test_handler_not_called_on_iteration(self) -> None:
        """Test that handler is not called on iteration."""
        registry = SkillCommandRegistry()
        command = create_test_command("iter-cmd")
        registry.register("iter-cmd", command)

        events: list[tuple[str, SkillCommand | None]] = []

        def handler(name: str, command: SkillCommand | None) -> None:
            events.append((name, command))

        registry.on_command_change(handler)

        # Clear events from initial notification
        events.clear()

        # Perform iteration
        for _ in registry:
            pass

        assert len(events) == 0


class TestReplaceOperation:
    """Tests for replace operation notifications."""

    def test_handler_called_on_replace(self) -> None:
        """Test that handler is called when command is replaced."""
        registry = SkillCommandRegistry()
        events: list[tuple[str, SkillCommand | None]] = []

        def handler(name: str, command: SkillCommand | None) -> None:
            events.append((name, command))

        command1 = create_test_command("replace-cmd", "Original")
        command2 = create_test_command("replace-cmd", "Replacement")

        registry.register("replace-cmd", command1)
        registry.on_command_change(handler)

        # Clear events from initial notification
        events.clear()

        registry.register("replace-cmd", command2, replace=True)

        assert len(events) == 1
        assert events[0][0] == "replace-cmd"
        assert events[0][1] is command2


class TestCommandChangeHandlerType:
    """Tests for CommandChangeHandler type alias."""

    def test_handler_type_accepts_callable(self) -> None:
        """Test that CommandChangeHandler type accepts valid callable."""

        def valid_handler(name: str, command: SkillCommand | None) -> None:
            pass

        # Should not raise
        handler: CommandChangeHandler = valid_handler
        assert callable(handler)

    def test_handler_type_with_lambda(self) -> None:
        """Test that lambda can be used as CommandChangeHandler."""
        handler: CommandChangeHandler = lambda name, cmd: None
        assert callable(handler)


class TestHandlerBehaviorEdgeCases:
    """Tests for handler behavior edge cases."""

    def test_handler_not_affected_by_other_registries(self) -> None:
        """Test that handler is only notified by its own registry."""
        registry1 = SkillCommandRegistry()
        registry2 = SkillCommandRegistry()

        events: list[str] = []

        def handler(name: str, command: SkillCommand | None) -> None:
            events.append(name)

        registry1.on_command_change(handler)

        command = create_test_command("cmd")
        registry2.register("cmd", command)

        assert len(events) == 0

    def test_multiple_registries_independent(self) -> None:
        """Test that multiple registries have independent handler sets."""
        registry1 = SkillCommandRegistry()
        registry2 = SkillCommandRegistry()

        events1: list[str] = []
        events2: list[str] = []

        def handler1(name: str, command: SkillCommand | None) -> None:
            events1.append(name)

        def handler2(name: str, command: SkillCommand | None) -> None:
            events2.append(name)

        registry1.on_command_change(handler1)
        registry2.on_command_change(handler2)

        command1 = create_test_command("cmd1")
        command2 = create_test_command("cmd2")

        registry1.register("cmd1", command1)
        registry2.register("cmd2", command2)

        assert events1 == ["cmd1"]
        assert events2 == ["cmd2"]

    def test_handler_order_preserved(self) -> None:
        """Test that handlers are called in registration order."""
        registry = SkillCommandRegistry()
        order: list[int] = []

        def handler1(_name: str, _cmd: SkillCommand | None) -> None:
            order.append(1)

        def handler2(_name: str, _cmd: SkillCommand | None) -> None:
            order.append(2)

        def handler3(_name: str, _cmd: SkillCommand | None) -> None:
            order.append(3)

        registry.on_command_change(handler1)
        registry.on_command_change(handler2)
        registry.on_command_change(handler3)

        command = create_test_command("cmd")
        registry.register("cmd", command)

        assert order == [1, 2, 3]


class TestNoSkillInternalsLeakage:
    """Tests that skills internals are not leaked through callbacks."""

    def test_callback_receives_command_not_skill(self) -> None:
        """Test that callback receives SkillCommand, not raw Skill."""
        registry = SkillCommandRegistry()
        received: list[Any] = []

        def handler(name: str, command: SkillCommand | None) -> None:
            received.append(command)

        command = create_test_command("test-cmd")
        registry.on_command_change(handler)

        registry.register("test-cmd", command)

        assert len(received) == 1
        assert isinstance(received[0], SkillCommand)
        assert received[0] is command
