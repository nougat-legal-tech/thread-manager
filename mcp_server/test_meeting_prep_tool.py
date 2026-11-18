"""Meeting-prep MCP responses must satisfy the generated-brief contract."""

from types import SimpleNamespace

import mcp_server.server as server


def _run(coroutine):
    try:
        return coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    coroutine.close()
    raise AssertionError("Mocked MCP meeting-prep call unexpectedly suspended")


def test_generate_meeting_prep_rejects_pending_scaffolds(monkeypatch):
    async def post(_ctx, _path, _body):
        return {
            "data": {
                "generateMeetingPrep": {
                    "meetingPrep": {
                        "prepId": "prep-pending",
                        "status": "pending",
                        "summary": "Prepare for this meeting.",
                    }
                }
            }
        }

    monkeypatch.setattr(server, "_post", post)

    result = _run(server.generate_meeting_prep(SimpleNamespace(), "Matter review"))

    assert result == {
        "success": False,
        "error": "No meeting brief was returned. Please retry.",
    }


def test_generate_meeting_prep_returns_a_generated_brief_and_forwards_force(monkeypatch):
    captured = {}
    brief = {"prepId": "prep-generated", "status": "generated", "summary": "Review the matter."}

    async def post(_ctx, path, body):
        captured.update(path=path, body=body)
        return {"data": {"generateMeetingPrep": {"meetingPrep": brief}}}

    monkeypatch.setattr(server, "_post", post)

    result = _run(
        server.generate_meeting_prep(SimpleNamespace(), "Matter review", force=True, app_id="2")
    )

    assert result == {"success": True, "meetingPrep": brief}
    assert captured["path"] == "/api/core/graphql/"
    assert captured["body"]["operationName"] == "GenerateMeetingPrep"
    assert captured["body"]["variables"]["input"]["force"] is True
    assert captured["body"]["variables"]["input"]["appId"] == "2"
