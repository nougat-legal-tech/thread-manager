"""The MCP bridge must preserve the authenticated caller for Django policy checks."""

from types import SimpleNamespace

import mcp_server.server as server


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True}


class _Client:
    def __init__(self, calls):
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, path, **kwargs):
        self.calls.append(("GET", path, kwargs))
        return _Response()

    async def post(self, path, **kwargs):
        self.calls.append(("POST", path, kwargs))
        return _Response()


def _run_mock_async(coroutine):
    """Run a mocked async call that must finish without real I/O or scheduling."""
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("Mocked MCP forwarding test attempted asynchronous I/O")


def test_mcp_case_tool_passes_caller_token_and_case_scope_to_django(monkeypatch):
    tokens = []
    calls = []
    monkeypatch.setattr(server, "_client", lambda token: tokens.append(token) or _Client(calls))
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(headers={"authorization": "Bearer signed-user-token"})
        )
    )

    result = _run_mock_async(server.get_case_metadata(ctx, "case-123"))

    assert result == {"ok": True}
    assert tokens == ["Bearer signed-user-token"]
    assert calls == [
        ("POST", "/api/core/get-case-metadata/", {"content": '{"caseId": "case-123"}'}),
    ]


def test_join_meeting_forwards_caller_token_scope_and_consent_flag(monkeypatch):
    tokens = []
    calls = []
    monkeypatch.setattr(server, "_client", lambda token: tokens.append(token) or _Client(calls))
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(headers={"authorization": "Bearer signed-user-token"})
        )
    )

    result = _run_mock_async(
        server.join_meeting(
            ctx,
            meeting_url="https://zoom.us/j/123456789",
            app_id="4",
            meeting_title="Status meeting",
            case_id="matter-123",
        )
    )

    assert result == {"ok": True}
    assert tokens == ["Bearer signed-user-token"]
    assert calls == [
        (
            "POST",
            "/api/core/meeting-sessions/",
            {
                "content": (
                    '{"meetingUrl": "https://zoom.us/j/123456789", '
                    '"meetingTitle": "Status meeting", "appId": "4", '
                    '"caseId": "matter-123", "participationMode": "proactive", '
                    '"consentGiven": true}'
                ),
            },
        ),
    ]


def test_assign_action_item_forwards_vienna_identity_with_authenticated_caller(monkeypatch):
    tokens = []
    calls = []
    monkeypatch.setattr(server, "_client", lambda token: tokens.append(token) or _Client(calls))
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(headers={"authorization": "Bearer signed-user-token"})
        )
    )

    result = _run_mock_async(
        server.assign_action_item(
            ctx,
            item_id="task-123",
            assignee_id="vienna@nougat.law",
        )
    )

    assert result == {"ok": True}
    assert tokens == ["Bearer signed-user-token"]
    assert calls == [
        (
            "POST",
            "/api/core/action-items/task-123/assign/",
            {"content": '{"assignedTo": ["vienna@nougat.law"]}'},
        ),
    ]


def test_edit_company_action_item_uses_universal_action_api(monkeypatch):
    tokens = []
    calls = []
    monkeypatch.setattr(server, "_client", lambda token: tokens.append(token) or _Client(calls))
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(headers={"authorization": "Bearer signed-user-token"})
        )
    )

    result = _run_mock_async(
        server.edit_case_action_items(
            ctx,
            case_id="juristai",
            operation="update",
            item_id="task-123",
            assigned_to=["vienna@nougat.law"],
            app_id="3",
        ),
    )

    assert result == {"ok": True}
    assert tokens == ["Bearer signed-user-token"]
    assert calls == [
        (
            "POST",
            "/api/edit-case-action-items/",
            {
                "content": '{"caseId": "juristai", "operation": "update", "itemId": "task-123", "assignedTo": ["vienna@nougat.law"], "appId": "3"}',
            },
        ),
    ]


def test_generic_action_item_api_writes_verified_summary_for_seriesai_case(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_client", lambda _token: _Client(calls))
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers={})))

    result = _run_mock_async(
        server.edit_case_action_items(
            ctx,
            case_id="juristai",
            operation="update",
            item_id="task-123",
            assigned_to=["vienna@nougat.law"],
            assistant_excerpt="Verified progress.",
            app_id="3",
        ),
    )

    assert result == {"ok": True}
    assert calls == [
        (
            "POST",
            "/api/edit-case-action-items/",
            {
                "content": '{"caseId": "juristai", "operation": "update", "itemId": "task-123", "assistantExcerpt": "Verified progress.", "assignedTo": ["vienna@nougat.law"], "appId": "3"}',
            },
        ),
    ]


def test_generic_bulk_action_api_can_assign_every_seriesai_task(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_client", lambda _token: _Client(calls))
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers={})))

    result = _run_mock_async(
        server.bulk_edit_case_action_items(
            ctx,
            case_id="juristai",
            operation="update",
            assignee="vienna@nougat.law",
            apply_to_all=True,
            app_id="3",
        ),
    )

    assert result == {"ok": True}
    assert calls == [
        (
            "POST",
            "/api/action-items/bulk/",
            {
                "content": '{"caseId": "juristai", "operation": "update", "assignee": "vienna@nougat.law", "applyToAll": true, "appId": "3"}',
            },
        ),
    ]


def test_mcp_create_new_unfiled_matter_forwards_without_case_or_docket_id(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_client", lambda _token: _Client(calls))
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers={})))

    result = _run_mock_async(
        server.create_new_case(
            ctx,
            app_id="2",
            case_name="Rowan Glass LLC v. Alpine Demo Services, Inc.",
            user_id="user-1",
        )
    )

    assert result == {"ok": True}
    assert calls == [
        (
            "POST",
            "/api/core/create-new-case/",
            {
                "content": (
                    '{"appId": "2", "caseName": '
                    '"Rowan Glass LLC v. Alpine Demo Services, Inc.", "userId": "user-1"}'
                )
            },
        ),
    ]


def test_mcp_zoom_disconnect_forwards_selected_app_scope(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_client", lambda _token: _Client(calls))
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers={})))

    result = _run_mock_async(
        server.disconnect_scheduling_conferencing(ctx, "zoom-2", app_id="4"),
    )

    assert result == {"ok": True}
    assert calls == [
            ("POST", "/api/core/integrations/conferencing/zoom-2/disconnect", {"content": '{"appId": "4"}'}),
    ]


def test_mcp_client_does_not_create_an_authorization_header_without_a_caller_token():
    client = server._client(None)

    assert "Authorization" not in client.headers



def test_mcp_client_forwards_bearer_token_to_django():
    client = server._client("signed-user-token")

    assert client.headers["Authorization"] == "Bearer signed-user-token"


def test_send_whatsapp_message_requires_confirmation_without_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_client", lambda _token: calls.append("client") or _Client([]))
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(headers={"authorization": "Bearer signed-user-token"})
        )
    )

    result = _run_mock_async(
        server.send_whatsapp_message(
            ctx,
            connection_id="connection-1",
            case_id="case-1",
            to="+15551234567",
            message="Please review the attached draft.",
        )
    )

    assert result["status"] == "confirmation_required"
    assert result["sent"] is False
    assert result["preview"] == {
        "connectionId": "connection-1",
        "caseId": "case-1",
        "to": "+15551234567",
        "message": "Please review the attached draft.",
        "contextMessageId": None,
        "appId": None,
    }
    assert calls == []


def test_send_whatsapp_message_dispatches_only_after_confirmation(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_client", lambda token: calls.append(("client", token)) or _Client(calls))
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(headers={"authorization": "Bearer signed-user-token"})
        )
    )

    result = _run_mock_async(
        server.send_whatsapp_message(
            ctx,
            connection_id="connection-1",
            case_id="case-1",
            to="+15551234567",
            message="Please review the attached draft.",
            confirm=True,
        )
    )

    assert result == {"ok": True}
    assert calls == [
        ("client", "Bearer signed-user-token"),
        (
            "POST",
            "/api/core/integrations/whatsapp/messages",
            {
                "content": '{"connectionId": "connection-1", "caseId": "case-1", "to": "+15551234567", "message": "Please review the attached draft."}',
            },
        ),
    ]
