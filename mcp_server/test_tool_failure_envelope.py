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
        "upstream_provider",
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
        "/api/core/latest-docket-entry/": "get_latest_docket_entry",
    }
    assert {prefix for prefix, _tools in server._ALTERNATE_READ_SOURCES} == set(source_tool_by_prefix)  # noqa: SLF001
    for prefix, tools in server._ALTERNATE_READ_SOURCES:  # noqa: SLF001
        assert tools
        assert source_tool_by_prefix[prefix] not in tools
        for tool in tools:
            assert callable(getattr(server, tool)), tool
            # Every alternate is a read: a search, or the public-record docket read.
            assert tool.startswith("search_") or tool == "get_public_latest_docket_entry", tool


def test_a_broken_docket_lookup_offers_the_public_record_api(monkeypatch):
    _client_returning(monkeypatch, _response("GET", 500, json={"detail": "boom"}))

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _run(server._get(object(), "/api/latest-docket-entry/"))  # noqa: SLF001

    recovery = _envelope(raised.value)["recovery"]
    assert recovery["alternates"] == ["get_public_latest_docket_entry"]
    assert "get_public_latest_docket_entry" in recovery["guidance"]


def test_the_public_record_read_is_not_its_own_alternate(monkeypatch):
    _client_returning(monkeypatch, _response("GET", 503, json={"detail": "down"}))

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _run(server._get(object(), "/api/public-docket/latest-entry/"))  # noqa: SLF001

    assert "alternates" not in _envelope(raised.value)["recovery"]


# --- Browser fallback for public records (spec A3, rung 4) --------------------


# No playbook is live (CourtListener refuses a headless browser), so these tests
# install one to check the rules that still apply when one is added.
_TEST_PLAYBOOKS = (("/api/core/latest-docket-entry/", "courtlistener_public_docket"),)


def test_no_browser_playbook_is_live():
    assert server._BROWSER_PLAYBOOKS == ()  # noqa: SLF001


def test_a_broken_public_docket_lookup_names_the_browser_playbook(monkeypatch):
    monkeypatch.setattr(server, "_BROWSER_PLAYBOOKS", _TEST_PLAYBOOKS)
    _client_returning(monkeypatch, _response("GET", 500, json={"detail": "boom"}))

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _run(server._get(object(), "/api/latest-docket-entry/"))  # noqa: SLF001

    recovery = _envelope(raised.value)["recovery"]
    assert recovery["browserPlaybook"] == "courtlistener_public_docket"
    assert "read-only" in recovery["guidance"]


def test_the_browser_is_never_offered_around_an_access_or_sign_in_refusal(monkeypatch):
    monkeypatch.setattr(server, "_BROWSER_PLAYBOOKS", _TEST_PLAYBOOKS)
    for status in (401, 403, 400):
        _client_returning(monkeypatch, _response("GET", status, json={"detail": "no"}))
        with pytest.raises(httpx.HTTPStatusError) as raised:
            _run(server._get(object(), "/api/latest-docket-entry/"))  # noqa: SLF001
        assert "browserPlaybook" not in _envelope(raised.value)["recovery"], status


def test_other_reads_and_all_writes_get_no_browser(monkeypatch):
    monkeypatch.setattr(server, "_BROWSER_PLAYBOOKS", _TEST_PLAYBOOKS)
    _client_returning(monkeypatch, _response("GET", 500, json={"detail": "boom"}))
    with pytest.raises(httpx.HTTPStatusError) as other_read:
        _run(server._get(object(), "/api/core/integrations/clio/matters/"))  # noqa: SLF001
    _client_returning(monkeypatch, _response("POST", 404, json={"detail": "missing"}))
    with pytest.raises(httpx.HTTPStatusError) as write:
        _run(server._post(object(), "/api/core/add-case/", {}))  # noqa: SLF001

    assert "browserPlaybook" not in _envelope(other_read.value)["recovery"]
    assert "browserPlaybook" not in _envelope(write.value)["recovery"]


# --- Readback after a write of unknown outcome (spec A3, rung 1) ---------------


def _client_by_method(monkeypatch, write, read):
    """The write gets ``write``; every GET (the readback) gets ``read``. Returns what was sent."""
    calls: list[tuple[str, str]] = []
    waits: list[float] = []

    class _MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def _answer(self, verb, outcome):
            async def send(path, *_args, **_kwargs):
                calls.append((verb, path))
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

            return send

        def __getattr__(self, verb):
            return self._answer(verb, read if verb == "get" else write)

    async def _no_wait(seconds):
        waits.append(seconds)

    monkeypatch.setattr(server, "_client", lambda _token: _MockClient())
    monkeypatch.setattr(server, "_token", lambda _ctx: "token")
    monkeypatch.setattr(server, "_retry_sleep", _no_wait)
    return calls, waits


_ITEM = "/api/core/action-items/item-1/"
_DATE = "/api/core/case-important-dates/case-1/date-1/"
_TIMEOUT = httpx.ReadTimeout("timed out")


def _ok(body):
    return _response("GET", 200, json=body)


def test_a_completion_that_landed_returns_the_item_with_a_note_and_is_not_resent(monkeypatch):
    calls, waits = _client_by_method(monkeypatch, _TIMEOUT, _ok({"id": "item-1", "completed": True}))

    result = _run(server._post(object(), "/api/action-items/item-1/complete/", {}))  # noqa: SLF001

    assert result["completed"] is True
    assert result["readback"]["outcome"] == "committed"
    assert "do not call this tool again" in result["readback"]["note"]
    assert calls == [("post", "/api/core/action-items/item-1/complete/"), ("get", _ITEM)]
    assert waits == [server._READBACK_DELAY_SECONDS]  # noqa: SLF001


def test_a_completion_that_did_not_land_is_reported_as_nothing_changed(monkeypatch):
    _client_by_method(monkeypatch, _TIMEOUT, _ok({"id": "item-1", "completed": False}))

    with pytest.raises(httpx.ReadTimeout) as raised:
        _run(server._post(object(), "/api/action-items/item-1/complete/", {}))  # noqa: SLF001

    envelope = _envelope(raised.value)
    assert envelope["error"]["class"] == "transient"
    assert envelope["error"]["sideEffectState"] == "none"
    assert envelope["error"]["readback"] == "not_committed"
    assert envelope["recovery"]["allowed"] == "retry_once"
    assert "NOT made" in envelope["recovery"]["guidance"]


def test_a_500_whose_readback_shows_nothing_changed_is_an_internal_failure_not_a_retry(monkeypatch):
    _client_by_method(monkeypatch, _response("POST", 500, json={"detail": "boom"}), _ok({"completed": False}))

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _run(server._post(object(), "/api/action-items/item-1/complete/", {}))  # noqa: SLF001

    error = _envelope(raised.value)["error"]
    assert (error["class"], error["sideEffectState"], error["retryable"]) == ("internal", "none", False)


@pytest.mark.parametrize(
    "read",
    [
        _response("GET", 500, json={"detail": "also down"}),
        _response("GET", 404, json={"detail": "gone"}),
        _ok({"id": "item-1"}),
        httpx.ReadTimeout("readback timed out"),
    ],
)
def test_a_readback_that_cannot_tell_leaves_the_outcome_unknown(monkeypatch, read):
    calls, _waits = _client_by_method(monkeypatch, _TIMEOUT, read)

    with pytest.raises(httpx.ReadTimeout) as raised:
        _run(server._post(object(), "/api/action-items/item-1/complete/", {}))  # noqa: SLF001

    envelope = _envelope(raised.value)
    assert envelope["error"]["class"] == "unknown_outcome"
    assert envelope["error"]["sideEffectState"] == "unknown"
    assert envelope["error"]["readback"] == "inconclusive"
    assert envelope["recovery"]["allowed"] == "verify"
    assert [verb for verb, _path in calls] == ["post", "get"]


def test_an_edit_is_committed_only_when_every_sent_field_reads_back(monkeypatch):
    _client_by_method(monkeypatch, _TIMEOUT, _ok({"title": "New", "priority": "high", "status": "open"}))
    result = _run(server._patch(object(), "/api/action-items/item-1/", {"title": "New", "priority": "high"}))  # noqa: SLF001
    assert result["readback"]["outcome"] == "committed"

    _client_by_method(monkeypatch, _TIMEOUT, _ok({"title": "Old", "priority": "high"}))
    with pytest.raises(httpx.ReadTimeout) as raised:
        _run(server._patch(object(), "/api/action-items/item-1/", {"title": "New", "priority": "high"}))  # noqa: SLF001
    # A different value may be the server normalising it: not proof either way.
    assert _envelope(raised.value)["error"]["readback"] == "inconclusive"


def test_an_edit_with_no_comparable_field_stays_unknown(monkeypatch):
    _client_by_method(monkeypatch, _TIMEOUT, _ok({"suggestedDueDate": "2026-12-01"}))
    with pytest.raises(httpx.ReadTimeout) as raised:
        _run(server._patch(object(), "/api/action-items/item-1/", {"suggestedDueDate": "2026-12-01"}))  # noqa: SLF001
    assert _envelope(raised.value)["error"]["class"] == "unknown_outcome"


def test_a_case_date_edit_reads_the_same_date_back(monkeypatch):
    calls, _waits = _client_by_method(monkeypatch, _TIMEOUT, _ok({"label": "Hearing", "date": "2026-12-01"}))

    result = _run(  # noqa: SLF001
        server._patch(object(), "/api/case-important-dates/case-1/date-1/", {"label": "Hearing", "date": "2026-12-01"})
    )

    assert result["readback"]["outcome"] == "committed"
    assert calls[-1] == ("get", _DATE)


def test_a_case_date_delete_is_settled_by_whether_the_date_still_exists(monkeypatch):
    _client_by_method(monkeypatch, _TIMEOUT, _response("GET", 404, json={"detail": "gone"}))
    result = _run(server._delete(object(), "/api/case-important-dates/case-1/date-1/", {"appId": "4"}))  # noqa: SLF001
    assert result["readback"]["outcome"] == "committed"

    _client_by_method(monkeypatch, _TIMEOUT, _ok({"label": "Hearing"}))
    with pytest.raises(httpx.ReadTimeout) as raised:
        _run(server._delete(object(), "/api/case-important-dates/case-1/date-1/", {"appId": "4"}))  # noqa: SLF001
    assert _envelope(raised.value)["error"]["readback"] == "not_committed"


@pytest.mark.parametrize(
    ("helper", "path"),
    [
        ("_post", "/api/action-items/"),  # a create without a caseId cannot be read back
        ("_post", "/api/action-items/item-1/assign/"),
        ("_post", "/api/core/thing/"),
    ],
)
def test_writes_without_a_readback_keep_todays_unknown_outcome_and_send_no_get(monkeypatch, helper, path):
    calls, waits = _client_by_method(monkeypatch, _TIMEOUT, _ok({"completed": True}))

    with pytest.raises(httpx.ReadTimeout) as raised:
        _run(getattr(server, helper)(object(), path, {}))

    assert "readback" not in _envelope(raised.value)["error"]
    assert [verb for verb, _path in calls] == ["post"]
    assert waits == []


def test_refused_writes_are_never_read_back(monkeypatch):
    calls, _waits = _client_by_method(monkeypatch, _response("POST", 409, json={"detail": "conflict"}), _ok({}))

    with pytest.raises(httpx.HTTPStatusError):
        _run(server._post(object(), "/api/action-items/item-1/complete/", {}))  # noqa: SLF001

    assert [verb for verb, _path in calls] == ["post"]


# --- Creates: read back by an id the sidecar chose ------------------------------


def _scripted_client(monkeypatch, posts, read):
    """POSTs answer from ``posts`` in order; GETs answer ``read``. Returns (calls, bodies)."""
    calls: list[tuple[str, str, dict | None]] = []
    bodies: list[dict] = []

    class _MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, path, content=None, **_kwargs):
            bodies.append(json.loads(content))
            calls.append(("post", path, None))
            outcome = posts[min(len(bodies) - 1, len(posts) - 1)]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        async def get(self, path, params=None, **_kwargs):
            calls.append(("get", path, params))
            if isinstance(read, Exception):
                raise read
            return read

    async def _no_wait(_seconds):
        return None

    monkeypatch.setattr(server, "_client", lambda _token: _MockClient())
    monkeypatch.setattr(server, "_token", lambda _ctx: "token")
    monkeypatch.setattr(server, "_retry_sleep", _no_wait)
    return calls, bodies


_CREATE = {"caseId": "case-1", "title": "File the reply", "appId": "4"}


def test_every_create_carries_an_id_the_sidecar_chose_and_keeps_a_callers_own(monkeypatch):
    _calls, bodies = _scripted_client(monkeypatch, [_response("POST", 201, json={"ok": True})], _ok({}))

    _run(server._post(object(), "/api/action-items/", dict(_CREATE)))  # noqa: SLF001
    _run(server._post(object(), "/api/action-items/", {**_CREATE, "itemId": "mine"}))  # noqa: SLF001
    _run(server._post(object(), "/api/case-important-dates/", {"caseId": "case-1", "label": "Hearing"}))  # noqa: SLF001
    _run(server._post(object(), "/api/core/thing/", {"caseId": "case-1"}))  # noqa: SLF001

    assert len(bodies[0]["itemId"]) == 36  # noqa: PLR2004 - a uuid4
    assert bodies[1]["itemId"] == "mine"
    assert len(bodies[2]["deadlineId"]) == 36  # noqa: PLR2004
    assert "itemId" not in bodies[3]
    assert "deadlineId" not in bodies[3]


def test_a_create_that_landed_is_found_by_its_id_and_not_sent_again(monkeypatch):
    calls, bodies = _scripted_client(monkeypatch, [_TIMEOUT], _ok({"itemId": "x", "title": "File the reply"}))

    result = _run(server._post(object(), "/api/action-items/", dict(_CREATE)))  # noqa: SLF001

    item_id = bodies[0]["itemId"]
    assert result["readback"]["outcome"] == "committed"
    assert calls == [
        ("post", "/api/core/action-items/", None),
        ("get", f"/api/core/action-items/{item_id}/", {"caseId": "case-1"}),
    ]


def test_a_create_that_is_not_there_is_sent_once_more_with_the_same_id(monkeypatch):
    calls, bodies = _scripted_client(
        monkeypatch,
        [_TIMEOUT, _response("POST", 201, json={"itemId": "x", "title": "File the reply"})],
        _response("GET", 404, json={"detail": "not found"}),
    )

    result = _run(server._post(object(), "/api/action-items/", dict(_CREATE)))  # noqa: SLF001

    assert result == {"itemId": "x", "title": "File the reply"}
    assert [verb for verb, _path, _params in calls] == ["post", "get", "post"]
    assert bodies[0] == bodies[1]  # the same id: a late first attempt is overwritten, not doubled


def test_a_create_whose_resend_also_fails_stays_unknown_and_must_not_be_created_again(monkeypatch):
    calls, bodies = _scripted_client(
        monkeypatch, [_TIMEOUT, _TIMEOUT], _response("GET", 404, json={"detail": "not found"})
    )

    with pytest.raises(httpx.ReadTimeout) as raised:
        _run(server._post(object(), "/api/action-items/", dict(_CREATE)))  # noqa: SLF001

    envelope = _envelope(raised.value)
    assert envelope["error"]["class"] == "unknown_outcome"
    assert envelope["error"]["readback"] == "absent_then_resent"
    assert envelope["error"]["attempts"] == 2  # noqa: PLR2004
    assert "Do not create it again" in envelope["recovery"]["guidance"]
    assert envelope["recovery"]["allowed"] == "verify"
    assert [verb for verb, _path, _params in calls] == ["post", "get", "post"]
    assert bodies[0]["itemId"] == bodies[1]["itemId"]


def test_a_resend_refused_by_django_is_reported_as_that_refusal(monkeypatch):
    _scripted_client(
        monkeypatch,
        [_TIMEOUT, _response("POST", 400, json={"detail": "bad"})],
        _response("GET", 404, json={"detail": "not found"}),
    )

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _run(server._post(object(), "/api/action-items/", dict(_CREATE)))  # noqa: SLF001

    error = _envelope(raised.value)["error"]
    assert (error["class"], error["sideEffectState"], error["readback"]) == (
        "invalid_input",
        "none",
        "absent_then_resent",
    )


@pytest.mark.parametrize(
    "read",
    [
        _response("GET", 405, json={"detail": "Method not allowed"}),  # a Django without the GET yet
        _response("GET", 500, json={"detail": "down"}),
        httpx.ReadTimeout("readback timed out"),
    ],
)
def test_a_create_readback_that_proves_nothing_never_resends(monkeypatch, read):
    calls, _bodies = _scripted_client(monkeypatch, [_TIMEOUT], read)

    with pytest.raises(httpx.ReadTimeout) as raised:
        _run(server._post(object(), "/api/action-items/", dict(_CREATE)))  # noqa: SLF001

    envelope = _envelope(raised.value)
    assert envelope["error"]["class"] == "unknown_outcome"
    assert envelope["error"]["readback"] == "inconclusive"
    assert [verb for verb, _path, _params in calls] == ["post", "get"]


def test_a_case_date_create_is_read_back_at_its_own_path_and_app(monkeypatch):
    calls, bodies = _scripted_client(monkeypatch, [_TIMEOUT], _ok({"label": "Hearing"}))

    result = _run(  # noqa: SLF001
        server._post(object(), "/api/case-important-dates/", {"caseId": "case-1", "label": "Hearing", "appId": "4"})
    )

    deadline_id = bodies[0]["deadlineId"]
    assert result["readback"]["outcome"] == "committed"
    assert calls[-1] == ("get", f"/api/core/case-important-dates/case-1/{deadline_id}/", {"appId": "4"})


# --- Durable failure events (spec A5) ---------------------------------------------


def _event_client(monkeypatch, tool_outcome, *, event_outcome=None):
    """Tool calls get ``tool_outcome``; POSTs to the events path are recorded."""
    import asyncio  # noqa: PLC0415 - only these tests need a real event loop

    recorded: list[dict] = []

    class _MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, path, content=None, **_kwargs):
            if path == server._FAILURE_EVENTS_PATH:  # noqa: SLF001
                recorded.append(json.loads(content))
                if isinstance(event_outcome, Exception):
                    raise event_outcome
                return _response("POST", 202, json={"eventId": "e1"})
            if isinstance(tool_outcome, Exception):
                raise tool_outcome
            return tool_outcome

        get = patch = delete = post

    async def _no_wait(_seconds):
        return None

    monkeypatch.setattr(server, "_client", lambda _token: _MockClient())
    monkeypatch.setattr(server, "_token", lambda _ctx: "token")
    monkeypatch.setattr(server, "_retry_sleep", _no_wait)

    async def call(coroutine):
        try:
            return await coroutine
        finally:
            await asyncio.gather(*list(server._PENDING_FAILURE_EVENTS))  # noqa: SLF001

    return recorded, lambda coroutine: asyncio.run(call(coroutine))


def test_failure_events_are_off_unless_switched_on(monkeypatch):
    monkeypatch.setattr(server, "_FAILURE_EVENTS_ENABLED", False)
    recorded, run = _event_client(monkeypatch, _response("GET", 500, json={"detail": "boom"}))

    with pytest.raises(httpx.HTTPStatusError):
        run(server._get(object(), "/api/core/thing/"))  # noqa: SLF001

    assert recorded == []


def test_a_failure_is_recorded_with_classes_and_path_but_never_detail(monkeypatch):
    monkeypatch.setattr(server, "_FAILURE_EVENTS_ENABLED", True)
    recorded, run = _event_client(
        monkeypatch, _response("POST", 409, json={"detail": "Smith v Jones memo", "code": "stale_item"})
    )

    with pytest.raises(httpx.HTTPStatusError):
        run(server._post(object(), "/api/core/thing/", {}))  # noqa: SLF001

    (event,) = recorded
    assert event == {
        "eventType": "tool_failure",
        "surface": "mcp_sidecar",
        "failureClass": "conflict",
        "sideEffectState": "none",
        "httpStatus": 409,
        "method": "POST",
        "attempts": 1,
        "retryable": False,
        "code": "stale_item",
        "recoveryAllowed": "repair",
        "path": "/api/core/thing/",
    }


def test_a_readback_that_found_the_write_is_recorded_as_a_recovery_outcome(monkeypatch):
    monkeypatch.setattr(server, "_FAILURE_EVENTS_ENABLED", True)
    recorded, run = _event_client(monkeypatch, httpx.ReadTimeout("timed out"))

    async def _found_readback():
        return "committed", {"completed": True}

    monkeypatch.setattr(server, "_readback_for", lambda *_args, **_kwargs: _found_readback)

    result = run(server._post(object(), "/api/action-items/item-1/complete/", {}))  # noqa: SLF001

    assert result["readback"]["outcome"] == "committed"
    assert [event["eventType"] for event in recorded] == ["recovery_outcome"]
    assert recorded[0]["readback"] == "committed"


def test_a_broken_events_endpoint_never_changes_the_tools_own_failure(monkeypatch):
    monkeypatch.setattr(server, "_FAILURE_EVENTS_ENABLED", True)
    recorded, run = _event_client(
        monkeypatch,
        _response("POST", 400, json={"detail": "bad"}),
        event_outcome=httpx.ConnectError("events down"),
    )

    with pytest.raises(httpx.HTTPStatusError) as raised:
        run(server._post(object(), "/api/core/thing/", {}))  # noqa: SLF001

    assert _envelope(raised.value)["error"]["class"] == "invalid_input"
    assert len(recorded) == 1


# --- Connector verdicts survive (spec A5) -----------------------------------------


def test_a_connector_that_says_not_retryable_is_not_retried(monkeypatch):
    sends, _waits = _client_returning(
        monkeypatch,
        _response("GET", 502, json={"error": "clio_error", "code": "clio_error", "message": "x", "retryable": False}),
    )

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _run(server._get(object(), "/api/core/integrations/clio/matters"))  # noqa: SLF001

    error = _envelope(raised.value)["error"]
    assert (error["class"], error["code"], error["retryable"]) == ("upstream_provider", "clio_error", False)
    assert len(sends) == 1


def test_a_connector_that_says_retryable_keeps_the_normal_retry(monkeypatch):
    sends, _waits = _client_returning(
        monkeypatch,
        _response("GET", 503, json={"error": "clio_unavailable", "code": "clio_unavailable", "retryable": True}),
    )

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _run(server._get(object(), "/api/core/integrations/clio/matters"))  # noqa: SLF001

    assert _envelope(raised.value)["error"]["class"] == "transient"
    assert len(sends) == _SENDS_WITH_ONE_RETRY


def test_a_write_the_connector_marks_not_retryable_is_still_an_unknown_outcome_on_5xx(monkeypatch):
    _client_returning(monkeypatch, _response("POST", 502, json={"code": "clio_error", "retryable": False}))

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _run(server._post(object(), "/api/core/integrations/clio/matters", {}))  # noqa: SLF001

    assert _envelope(raised.value)["error"]["class"] == "unknown_outcome"
