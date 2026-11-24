"""The assistant can propose a recurring automation but never switch one on by itself.

Contract: docs/architecture/
agentic-tool-recovery-and-scoped-automations-spec.md (B5).
"""

import pytest

from mcp_server import server

WEEKLY = {"frequency": "weekly", "hour": 8, "minute": 0, "daysOfWeek": [0]}


def _run(coroutine):
    try:
        yielded = coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    coroutine.close()
    message = f"Mocked MCP operation unexpectedly suspended: {yielded!r}"
    raise AssertionError(message)


@pytest.fixture
def calls(monkeypatch):
    made = []

    async def fake_post(ctx, path, body):
        made.append(("POST", path, body))
        return {"ok": True}

    async def fake_get(ctx, path, params=None):
        made.append(("GET", path, params))
        return {"automations": []}

    async def fake_delete(ctx, path, params=None):
        made.append(("DELETE", path, params))
        return {"deleted": True}

    monkeypatch.setattr(server, "_post", fake_post)
    monkeypatch.setattr(server, "_get", fake_get)
    monkeypatch.setattr(server, "_delete", fake_delete)
    return made


def test_propose_sends_a_prompt_automation_to_the_proposal_endpoint(calls):
    _run(
        server.propose_automation(
            object(),
            name="Monday docket brief",
            cadence=WEEKLY,
            prompt="Summarise new docket entries on my cases.",
            time_zone="America/New_York",
        )
    )

    assert calls == [
        (
            "POST",
            "/api/core/automations",
            {
                "name": "Monday docket brief",
                "kind": "agent_prompt",
                "spec": {"prompt": "Summarise new docket entries on my cases."},
                "cadence": WEEKLY,
                "scope": {"type": "user", "id": None},
                "timezone": "America/New_York",
                    "actionPolicy": "read_only",
                "via": "chat",
            },
        )
    ]


def test_propose_builds_a_firm_report_with_delivery_channels(calls):
    _run(
        server.propose_automation(
            object(),
            name="Weekly financials",
            cadence=WEEKLY,
            kind="report",
            report_id="financial",
            scope_type="firm",
            delivery_channels=["email", "slack"],
            via="slack",
        )
    )

    body = calls[0][2]
    assert body["spec"] == {"reportId": "financial"}
    assert body["scope"] == {"type": "firm", "id": None}
    assert body["delivery"] == {"channels": ["email", "slack"]}
    assert body["via"] == "slack"
    assert "timezone" not in body


@pytest.mark.parametrize("action", ["confirm", "approve", "delete"])
def test_activating_or_deleting_needs_the_users_explicit_yes(calls, action):
    result = _run(server.manage_automation(object(), "auto-1", action))

    assert result["error"] == "confirmation_required"
    assert action in result["message"]
    assert calls == []


def test_confirmed_actions_reach_the_right_endpoint_with_a_quoted_id(calls):
    _run(server.manage_automation(object(), "a/b", "confirm", user_confirmed=True))
    _run(server.manage_automation(object(), "auto-1", "approve", user_confirmed=True))
    _run(server.manage_automation(object(), "auto-1", "delete", user_confirmed=True))

    assert calls == [
        ("POST", "/api/core/automations/a%2Fb/confirm", {}),
        ("POST", "/api/core/automations/auto-1/approve", {}),
        ("DELETE", "/api/core/automations/auto-1", None),
    ]


def test_pause_and_resume_do_not_need_a_second_confirmation(calls):
    _run(server.manage_automation(object(), "auto-1", "pause"))
    _run(server.manage_automation(object(), "auto-1", "resume"))

    assert [call[1] for call in calls] == [
        "/api/core/automations/auto-1/pause",
        "/api/core/automations/auto-1/resume",
    ]


def test_unknown_actions_are_refused_without_a_request(calls):
    result = _run(server.manage_automation(object(), "auto-1", "run_now", user_confirmed=True))

    assert result["error"] == "invalid_automation_action"
    assert "confirm" in result["allowedActions"]
    assert calls == []


def test_list_passes_the_scope_filter(calls):
    _run(server.list_automations(object(), scope_type="firm"))

    assert calls == [("GET", "/api/core/automations", {"scopeType": "firm"})]


def test_check_back_sets_one_personal_read_only_follow_up_without_confirmation(calls):
    _run(
        server.check_back(
            object(),
            when="in 2 hours",
            note="Check whether the court entered the order and report the docket text.",
            only_if="the docket still has no order",
            app_id="2",
            case_id="case-1",
            time_zone="America/New_York",
        )
    )

    assert calls == [
        (
            "POST",
            "/api/core/check-backs",
            {
                "when": "in 2 hours",
                "note": "Check whether the court entered the order and report the docket text.",
                "condition": "the docket still has no order",
                "appId": "2",
                "caseId": "case-1",
                "timezone": "America/New_York",
            },
        )
    ]


def test_check_back_lists_and_cancels_only_the_requested_item(calls):
    _run(server.check_back(object()))
    _run(server.check_back(object(), cancel="cb/one"))
    _run(server.check_back(object(), cancel="all"))

    assert calls == [
        ("GET", "/api/core/check-backs", None),
        ("DELETE", "/api/core/check-backs/cb%2Fone", None),
        ("DELETE", "/api/core/check-backs/all", None),
    ]


def test_check_back_rejects_partial_schedule_without_a_request(calls):
    result = _run(server.check_back(object(), note="Check the docket later"))

    assert result == {
        "error": "invalid_check_back",
        "message": "Setting a check-back needs both when and note.",
    }
    assert calls == []


def test_a_member_can_mute_and_unmute_without_a_second_confirmation(calls):
    _run(server.manage_automation(object(), "auto-1", "mute"))
    _run(server.manage_automation(object(), "auto-1", "unmute"))

    assert [call[1] for call in calls] == [
        "/api/core/automations/auto-1/mute",
        "/api/core/automations/auto-1/unmute",
    ]


def test_suggesting_templates_reads_the_role_catalogue(calls):
    _run(server.suggest_automations(object(), app_id="2"))

    assert calls == [("GET", "/api/core/automations/templates", {"appId": "2"})]


def test_using_a_template_only_drafts_it(calls):
    _run(server.use_automation_template(object(), template_id="daily/brief", app_id="2", time_zone="America/New_York"))

    assert calls == [
        (
            "POST",
            "/api/core/automations/templates/daily%2Fbrief/propose",
            {"appId": "2", "timezone": "America/New_York"},
        )
    ]
