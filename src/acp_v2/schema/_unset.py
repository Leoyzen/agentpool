"""Sentinel for three-state patch fields.

In v2, patch fields distinguish three states:
- UNSET: field omitted -> leave previous value unchanged
- None: field explicitly null -> clear the value
- concrete value: field set -> replace the value
"""

from __future__ import annotations

from typing import Any

from pydantic_core import core_schema


class UnsetType:
    """Singleton sentinel representing an omitted patch field."""

    _instance: UnsetType | None = None

    def __new__(cls) -> UnsetType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False

    def __copy__(self) -> UnsetType:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> UnsetType:
        return self

    def __getstate__(self) -> dict[str, object]:
        return {}

    def __setstate__(self, state: dict[str, object]) -> None:
        pass

    def __reduce__(self) -> tuple[type[UnsetType], tuple[()]]:
        return (UnsetType, ())

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            cls._validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                cls._serialize,
            ),
        )

    @classmethod
    def _validate(cls, value: Any) -> Any:
        if isinstance(value, UnsetType):
            return value
        return value

    @classmethod
    def _serialize(cls, value: Any) -> Any:
        if isinstance(value, UnsetType):
            return None
        return value


_UNSET: UnsetType = UnsetType()


def is_unset(value: object) -> bool:
    """Check if a value is the UNSET sentinel."""
    return value is _UNSET or isinstance(value, UnsetType)
