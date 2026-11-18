"""Every failed Django call carries one machine-readable failure envelope.

Contract: docs/architecture/agentic-tool-recovery-and-scoped-automations-spec.md (A2).
The envelope is what lets callers tell a refused write (safe to repair and
retry) from a write whose outcome is unknown (never safe to re-send).
"""

import json

import httpx
import pytest

from mcp_server import server

_BASE = "https://api.test"
_RETRY_AFTER_SECONDS = 12
_SENDS_WITH_ONE_RETRY = 2


def _run(coroutine):
    try:
        yielded = coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    coroutine.close()
    message = f"Mocked MCP operation unexpectedly suspended: {yielded!r}"
    raise AssertionError(message)


def _envelope(error: Exception) -> dict:
    summary, marker, payload = str(error).partition(f"\n{server.TOOL_FAILURE_MARKER}")
    assert marker, f"no failure envelope in: {error}"
    assert summary
    return json.loads(payload)


def _client_returning(monkeypatch, *outcomes):
    """Script Django's answers in order (the last repeats); returns sends and waits."""
    sends: list[int] = []
    waits: list[float] = []

    class _MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def _send(self, *_args, **_kwargs):
            outcome = outcomes[min(len(sends), len(outcomes) - 1)]
            sends.append(1)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        get = post = patch = delete = _send

    async def _no_wait(seconds):
        waits.append(seconds)

    monkeypatch.setattr(server, "_client", lambda _token: _MockClient())
    monkeypatch.setattr(server, "_token", lambda _ctx: "token")
    monkeypatch.setattr(server, "_retry_sleep", _no_wait)
    return sends, waits


def _response(method: str, status: int, **kwargs) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request(method, f"{_BASE}/api/core/thing/"), **kwargs)


@pytest.mark.parametrize(
    ("status", "expected_class", "user_action"),
    [
        (400, "invalid_input", None),
        (401, "reauth_required", "sign_in_again"),
        (403, "permission_denied", "ask_an_admin"),
        (404, "not_found", None),
        (409, "conflict", None),
        (422, "invalid_input", None),
    ],
)
def test_refused_write_is_classified_and_proven_side_effect_free(monkeypatch, status, expected_class, user_action):
    _client_returning(monkeypatch, _response("POST", status, json={"detail": "Nope."}))

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _run(server._post(object(), "/api/core/thing/", {}))  # noqa: SLF001

    error = _envelope(raised.value)["error"]
    assert error["class"] == expected_class
    assert error["httpStatus"] == status
    assert error["sideEffectState"] == "none"
    assert error["retryable"] is False
    assert error["userActionRequired"] == user_action
    assert error["detail"] == "Nope."
    assert error["method"] == "POST"


def test_failed_read_is_never_an_unknown_outcome(monkeypatch):
    _client_returning(monkeypatch, _response("GET", 500, text="<html>boom</html>"))

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _run(server._get(object(), "/api/core/thing/"))  # noqa: SLF001

    error = _envelope(raised.value)["error"]
    assert error["class"] == "internal"
    assert error["sideEffectState"] == "none"
    assert error["detail"] is None


@pytest.mark.parametrize("helper", ["_post", "_patch"])
def test_server_error_on_a_write_is_an_unknown_outcome_and_not_retryable(monkeypatch, helper):
    _client_returning(monkeypatch, _response("POST", 502, json={"detail": "Bad gateway"}))

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _run(getattr(server, helper)(object(), "/api/core/thing/", {}))

    error = _envelope(raised.value)["error"]
    assert error["class"] == "unknown_outcome"
    assert error["sideEffectState"] == "unknown"
    assert error["retryable"] is False


def test_rate_limit_is_transient_with_retry_after(monkeypatch):
    _client_returning(
        monkeypatch,
        _response("POST", 429, json={"detail": "Slow down"}, headers={"Retry-After": "12"}),
    )

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _run(server._post(object(), "/api/core/thing/", {}))  # noqa: SLF001

    error = _envelope(raised.value)["error"]
    assert error["class"] == "transient"
    assert error["retryable"] is True
    assert error["retryAfterSeconds"] == _RETRY_AFTER_SECONDS
    assert error["sideEffectState"] == "none"


def test_read_timeout_on_a_write_is_unknown_but_on_a_read_is_retryable(monkeypatch):
    _client_returning(monkeypatch, httpx.ReadTimeout("timed out"))

    with pytest.raises(httpx.ReadTimeout) as write_error:
        _run(server._post(object(), "/api/core/thing/", {}))  # noqa: SLF001
    with pytest.raises(httpx.ReadTimeout) as read_error:
        _run(server._get(object(), "/api/core/thing/"))  # noqa: SLF001

    assert _envelope(write_error.value)["error"]["class"] == "unknown_outcome"
    assert _envelope(write_error.value)["error"]["retryable"] is False
    assert _envelope(read_error.value)["error"]["class"] == "transient"
    assert _envelope(read_error.value)["error"]["retryable"] is True


def test_connection_that_never_opened_is_safe_to_retry_even_for_a_write(monkeypatch):
    _client_returning(monkeypatch, httpx.ConnectError("refused"))

    with pytest.raises(httpx.ConnectError) as raised:
        _run(server._delete(object(), "/api/core/thing/"))  # noqa: SLF001

    error = _envelope(raised.value)["error"]
    assert error["class"] == "transient"
    assert error["sideEffectState"] == "none"
    assert error["retryable"] is True
    assert error["httpStatus"] is None


def test_body_codes_override_the_status_class(monkeypatch):
    _client_returning(
        monkeypatch,
        _response(
            "POST",
            400,
            json={
                "error": "confirmation_required",
                "message": "Ask the user to confirm.",
            },
        ),
    )
    with pytest.raises(httpx.HTTPStatusError) as confirm:
        _run(server._post(object(), "/api/core/thing/", {}))  # noqa: SLF001

    _client_returning(
        monkeypatch,
        _response(
            "GET",
            400,
            json={
                "code": "whatsapp_authorization_expired",
                "reauthorizationRequired": True,
            },
        ),
    )
    with pytest.raises(httpx.HTTPStatusError) as reauth:
        _run(server._get(object(), "/api/core/thing/"))  # noqa: SLF001

    confirm_error = _envelope(confirm.value)["error"]
    assert confirm_error["class"] == "needs_confirmation"
    assert confirm_error["code"] == "confirmation_required"
    assert confirm_error["detail"] == "Ask the user to confirm."
    assert confirm_error["userActionRequired"] == "confirm"
    reauth_error = _envelope(reauth.value)["error"]
    assert reauth_error["class"] == "reauth_required"
    assert reauth_error["userActionRequired"] == "reconnect_integration"


def test_prose_summary_still_leads_and_keeps_the_original_response(monkeypatch):
    response = _response("GET", 400, json={"detail": "Connect DocuSign from Connections first."})
    _client_returning(monkeypatch, response)

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _run(server._get(object(), "/api/core/thing/"))  # noqa: SLF001

    summary = str(raised.value).partition(f"\n{server.TOOL_FAILURE_MARKER}")[0]
    assert summary.endswith("Detail: Connect DocuSign from Connections first.")
    assert raised.value.response is response


def test_every_failure_is_logged_once_without_detail_or_resource_ids(monkeypatch, caplog):
    _client_returning(
        monkeypatch,
        _response("POST", 403, json={"detail": "Matter 'Rivera v. Acme' is restricted."}),
    )

    with (
        caplog.at_level("WARNING", logger="juristai.mcp.tool_failure"),
        pytest.raises(httpx.HTTPStatusError),
    ):
        _run(server._post(object(), "/api/core/cases/Rivera-Case-42/thing/", {}))  # noqa: SLF001

    records = [record.getMessage() for record in caplog.records if record.name == "juristai.mcp.tool_failure"]
    assert len(records) == 1
    assert "class=permission_denied" in records[0]
    assert "status=403" in records[0]
    assert "side_effect=none" in records[0]
    assert "Rivera" not in records[0]
    assert "Case-42" not in records[0]


# --- Automatic retry and recovery guidance (spec A3) --------------------------


def test_transient_read_failure_is_retried_once_and_can_succeed(monkeypatch):
    sends, waits = _client_returning(
        monkeypatch,
        httpx.ReadTimeout("timed out"),
        _response("GET", 200, json={"ok": True}),
    )

    assert _run(server._get(object(), "/api/core/thing/")) == {"ok": True}  # noqa: SLF001
    assert len(sends) == _SENDS_WITH_ONE_RETRY
    assert waits == [server._RETRY_DEFAULT_DELAY_SECONDS]  # noqa: SLF001


def test_retry_is_bounded_and_the_envelope_reports_the_attempts(monkeypatch):
    sends, _waits = _client_returning(monkeypatch, _response("GET", 503, json={"detail": "busy"}))

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _run(server._get(object(), "/api/core/thing/"))  # noqa: SLF001

    envelope = _envelope(raised.value)
    assert len(sends) == _SENDS_WITH_ONE_RETRY
    assert envelope["error"]["attempts"] == _SENDS_WITH_ONE_RETRY
    assert envelope["recovery"]["allowed"] == "retry_once"


@pytest.mark.parametrize(
    "outcome",
    [
        httpx.ReadTimeout("timed out"),
        _response("POST", 502, json={"detail": "Bad gateway"}),
        _response("POST", 500, json={"detail": "boom"}),
    ],
)
def test_a_write_that_may_have_landed_is_never_resent(monkeypatch, outcome):
    sends, waits = _client_returning(monkeypatch, outcome)

    with pytest.raises((httpx.HTTPStatusError, httpx.ReadTimeout)) as raised:
        _run(server._post(object(), "/api/core/thing/", {}))  # noqa: SLF001

    envelope = _envelope(raised.value)
    assert len(sends) == 1
    assert waits == []
    assert envelope["error"]["attempts"] == 1
    assert envelope["recovery"]["allowed"] == "verify"
    assert "Do NOT call this tool again" in envelope["recovery"]["guidance"]


def test_a_write_that_never_left_is_retried(monkeypatch):
    sends, _waits = _client_returning(
        monkeypatch,
        httpx.ConnectError("refused"),
        _response("POST", 200, json={"created": True}),
    )

    assert _run(server._post(object(), "/api/core/thing/", {})) == {"created": True}  # noqa: SLF001
    assert len(sends) == _SENDS_WITH_ONE_RETRY


def test_retry_after_is_honoured_when_short_and_skipped_when_long(monkeypatch):
    sends, waits = _client_returning(
        monkeypatch,
        _response("GET", 429, headers={"Retry-After": "2"}),
        _response("GET", 200, json={"ok": True}),
    )
    assert _run(server._get(object(), "/api/core/thing/")) == {"ok": True}  # noqa: SLF001
    assert (len(sends), waits) == (_SENDS_WITH_ONE_RETRY, [2.0])

    sends, waits = _client_returning(monkeypatch, _response("GET", 429, headers={"Retry-After": "120"}))
    with pytest.raises(httpx.HTTPStatusError):
        _run(server._get(object(), "/api/core/thing/"))  # noqa: SLF001
    assert (len(sends), waits) == (1, [])


def test_non_transient_failures_are_not_retried_and_carry_their_next_step(monkeypatch):
    expected = {
        400: "repair",
        404: "repair",
        409: "repair",
        401: "ask_user",
        403: "ask_user",
        500: "stop",
    }
    for status, allowed in expected.items():
        sends, _waits = _client_returning(monkeypatch, _response("GET", status, json={"detail": "x"}))
        with pytest.raises(httpx.HTTPStatusError) as raised:
            _run(server._get(object(), "/api/core/thing/"))  # noqa: SLF001
        assert len(sends) == 1, status
        assert _envelope(raised.value)["recovery"]["allowed"] == allowed, status


def test_retries_can_be_switched_off(monkeypatch):
    sends, _waits = _client_returning(monkeypatch, httpx.ReadTimeout("timed out"))
    monkeypatch.setattr(server, "_TRANSIENT_RETRIES", 0)

    with pytest.raises(httpx.ReadTimeout):
        _run(server._get(object(), "/api/core/thing/"))  # noqa: SLF001
    assert len(sends) == 1


def test_every_failure_class_has_recovery_guidance():
    classes = {
        "transient",
        "invalid_input",
        "not_found",
        "conflict",
        "unknown_outcome",
        "needs_confirmation",
        "reauth_required",
        "permission_denied",
        "internal",
    }
    assert set(server._RECOVERY_BY_CLASS) == classes  # noqa: SLF001


# --- Alternate sources for failed reads (spec A3, rung 3) ---------------------

_IMANAGE_SEARCH = "/api/core/integrations/imanage/agent/documents/search"


def test_a_failed_document_search_names_other_sources_to_try(monkeypatch):
    _client_returning(monkeypatch, _response("GET", 400, json={"detail": "Connect iManage first."}))

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _run(server._get(object(), _IMANAGE_SEARCH))  # noqa: SLF001

    recovery = _envelope(raised.value)["recovery"]
    assert recovery["alternates"] == [
        "search_documents",
        "search_netdocuments",
        "search_sharepoint_documents",
    ]
    assert "you may try ONE of search_documents" in recovery["guidance"]
    assert "say which source" in recovery["guidance"]


def test_alternates_are_offered_after_the_automatic_retry_also_fails(monkeypatch):
    sends, _waits = _client_returning(monkeypatch, _response("GET", 503, json={"detail": "busy"}))

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _run(server._get(object(), _IMANAGE_SEARCH))  # noqa: SLF001

    envelope = _envelope(raised.value)
    assert len(sends) == _SENDS_WITH_ONE_RETRY
    assert envelope["recovery"]["allowed"] == "retry_once"
    assert envelope["recovery"]["alternates"][0] == "search_documents"


def test_reads_with_no_equivalent_source_and_all_writes_get_no_alternates(monkeypatch):
    _client_returning(monkeypatch, _response("GET", 500, json={"detail": "boom"}))
    with pytest.raises(httpx.HTTPStatusError) as plain_read:
        _run(server._get(object(), "/api/core/integrations/clio/matters/"))  # noqa: SLF001

    _client_returning(monkeypatch, _response("POST", 400, json={"detail": "bad"}))
    with pytest.raises(httpx.HTTPStatusError) as write:
        _run(server._post(object(), "/api/core/add-case/", {}))  # noqa: SLF001

    assert "alternates" not in _envelope(plain_read.value)["recovery"]
    assert "alternates" not in _envelope(write.value)["recovery"]


def test_a_search_sent_as_post_is_a_read_not_a_write_of_unknown_outcome(monkeypatch):
    sends, _waits = _client_returning(
        monkeypatch,
        httpx.ReadTimeout("timed out"),
        _response("POST", 200, json={"results": []}),
    )
    assert _run(server._post(object(), "/api/rag-query-proxy/", {})) == {"results": []}  # noqa: SLF001
    assert len(sends) == _SENDS_WITH_ONE_RETRY

    _client_returning(monkeypatch, _response("POST", 502, json={"detail": "Bad gateway"}))
    with pytest.raises(httpx.HTTPStatusError) as raised:
        _run(server._post(object(), "/api/rag-query-proxy/", {}))  # noqa: SLF001

    envelope = _envelope(raised.value)
    assert envelope["error"]["class"] == "transient"
    assert envelope["error"]["sideEffectState"] == "none"
    assert envelope["error"]["method"] == "POST"
    assert envelope["recovery"]["alternates"] == [
        "search_imanage_documents",
        "search_netdocuments",
        "search_sharepoint_documents",
    ]


def test_every_alternate_is_a_real_read_only_search_tool_and_never_itself():
    source_tool_by_prefix = {
        "/api/core/integrations/netdocuments/search": "search_netdocuments",
        "/api/core/integrations/imanage/agent/documents/search": "search_imanage_documents",
        "/api/core/integrations/sharepoint/agent/documents/search": "search_sharepoint_documents",
        "/api/core/integrations/relativity/documents/search": "search_relativity_documents",
        "/api/core/rag-query-proxy/": "search_documents",
    }
    assert {prefix for prefix, _tools in server._ALTERNATE_READ_SOURCES} == set(  # noqa: SLF001
        source_tool_by_prefix
    )
    for prefix, tools in server._ALTERNATE_READ_SOURCES:  # noqa: SLF001
        assert tools
        assert source_tool_by_prefix[prefix] not in tools
        for tool in tools:
            assert callable(getattr(server, tool)), tool
            assert tool.startswith("search_"), tool
