from config.middleware import resolve_request_id


def test_accepts_a_safe_request_id() -> None:
    assert resolve_request_id("trace-123") == "trace-123"


def test_replaces_unsafe_or_missing_request_ids() -> None:
    for raw in (None, "", "bad id", "line\nbreak", "a" * 200, " leading"):
        resolved = resolve_request_id(raw)
        assert resolved != raw
        assert " " not in resolved
        assert "\n" not in resolved
