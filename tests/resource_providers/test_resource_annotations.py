"""Tests for ResourceAnnotations and ResourceInfo.size."""

from __future__ import annotations

from agentpool.resource_providers.resource_info import ResourceAnnotations, ResourceInfo


class TestResourceAnnotations:
    """Test ResourceAnnotations typed model."""

    def test_default_values(self) -> None:
        ann = ResourceAnnotations()
        assert ann.audience == []
        assert ann.priority == 0.5
        assert ann.last_modified is None
        assert ann.extra == {}

    def test_from_dict_full(self) -> None:
        data = {
            "audience": ["assistant"],
            "priority": 0.9,
            "lastModified": "2025-01-01T00:00:00Z",
            "customField": "value",
        }
        ann = ResourceAnnotations.from_dict(data)
        assert ann.audience == ["assistant"]
        assert ann.priority == 0.9
        assert ann.last_modified == "2025-01-01T00:00:00Z"
        assert ann.extra == {"customField": "value"}

    def test_from_dict_empty(self) -> None:
        ann = ResourceAnnotations.from_dict({})
        assert ann.audience == []
        assert ann.priority == 0.5
        assert ann.last_modified is None
        assert ann.extra == {}

    def test_from_dict_none_values(self) -> None:
        ann = ResourceAnnotations.from_dict({"audience": None, "priority": None})
        assert ann.audience == []
        assert ann.priority == 0.5

    def test_to_dict_roundtrip(self) -> None:
        data = {"audience": ["user"], "priority": 0.8, "custom": "field"}
        ann = ResourceAnnotations.from_dict(data)
        result = ann.to_dict()
        assert result["audience"] == ["user"]
        assert result["priority"] == 0.8
        assert result["custom"] == "field"

    def test_to_dict_defaults_omitted(self) -> None:
        ann = ResourceAnnotations()
        result = ann.to_dict()
        # Default priority (0.5) and empty audience should be omitted
        assert "priority" not in result
        assert "audience" not in result


class TestResourceInfoSize:
    """Test ResourceInfo.size field."""

    def test_size_default_none(self) -> None:
        info = ResourceInfo(name="test", uri="test://a")
        assert info.size is None

    def test_size_set(self) -> None:
        info = ResourceInfo(name="test", uri="test://a", size=1024)
        assert info.size == 1024


class TestResourceInfoTypedAnnotations:
    """Test ResourceInfo.typed_annotations property."""

    def test_typed_annotations_empty(self) -> None:
        info = ResourceInfo(name="test", uri="test://a", annotations={})
        ann = info.typed_annotations
        assert isinstance(ann, ResourceAnnotations)
        assert ann.audience == []

    def test_typed_annotations_with_data(self) -> None:
        info = ResourceInfo(
            name="test",
            uri="test://a",
            annotations={"audience": ["assistant"], "priority": 0.9},
        )
        ann = info.typed_annotations
        assert ann.audience == ["assistant"]
        assert ann.priority == 0.9
