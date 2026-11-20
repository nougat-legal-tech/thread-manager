import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


MODULE_PATH = Path(__file__).with_name("server.py")
SPEC = importlib.util.spec_from_file_location("chatbot_mcp_server_function_map_test", MODULE_PATH)
server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(server)


def _run_immediate(coroutine):
    """Resolve MCP coroutines that complete without an event-loop suspension."""
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    finally:
        coroutine.close()
    raise AssertionError("Expected this provider-free MCP operation to complete immediately")


def test_function_map_returns_category_table_of_contents_then_selected_schemas():
    index = _run_immediate(server.browse_function_map(object()))

    assert index["version"] == 1
    assert index["categories"]
    # The map merges every callable MCP tool with Django chat-run guidance records.
    registered_tools = server.mcp._tool_manager.list_tools()
    callable_tools = {tool.name for tool in registered_tools} - {"browse_function_map"}
    assert sum(item["count"] for item in index["categories"]) >= len(callable_tools)
    docusign_list = next(tool for tool in registered_tools if tool.name == "list_docusign_envelopes")
    assert "case_id" not in docusign_list.parameters.get("required", [])
    category = next(item for item in index["categories"] if item["id"] == "case_management")
    assert category["count"] > 0
    assert category["subcategories"]

    selected = _run_immediate(server.browse_function_map(object(), category["id"]))
    assert selected["category"]["id"] == "case_management"
    assert selected["functions"]
    assert all(record["categoryId"] == "case_management" for record in selected["functions"])
    assert all("inputSchema" in record for record in selected["functions"] if "mcp" in record["surfaces"])
    assert all(record["name"] != "browse_function_map" for record in selected["functions"])


def test_case_task_aliases_use_verified_shared_operation_ids():
    selected = _run_immediate(server.browse_function_map(object(), "case_management"))
    records = {record["name"]: record for record in selected["functions"]}
    expected = {
        "assign_action_item": ("assign_case_action_item", "assign-action-item"),
        "complete_action_item": ("complete_case_action_item", "complete-action-item"),
        "create_action_item": ("create_case_action_item", "create-action-item"),
        "update_action_item": ("update_case_action_item", "update-action-item"),
        "list_action_items": ("retrieve_case_action_items", "list-action-items"),
    }

    for tool_name, (alias_name, operation_id) in expected.items():
        alias = next(
            item for item in records[tool_name]["callerAliases"] if item["name"] == alias_name
        )
        assert alias["operationId"] == operation_id
        assert alias["verification"] == "verified_shared_operation_id"


def test_action_item_guidance_uses_task_ids_and_continues_vienna_work():
    selected = _run_immediate(server.browse_function_map(object(), "case_management"))
    records = {record["name"]: record for record in selected["functions"]}
    assign_alias = next(
        alias
        for alias in records["assign_action_item"]["callerAliases"]
        if alias["name"] == "assign_case_action_item"
    )
    complete_alias = next(
        alias
        for alias in records["complete_action_item"]["callerAliases"]
        if alias["name"] == "complete_case_action_item"
    )
    update_alias = next(
        alias
        for alias in records["update_action_item"]["callerAliases"]
        if alias["name"] == "update_case_action_item"
    )

    assert assign_alias["argumentBindings"] == {
        "actionItemId": "item_id",
        "assigneeEmail": "assignee_id",
    }
    assert "never its title" in assign_alias["invocationGuidance"]
    assert "vienna@nougat.law" in assign_alias["invocationGuidance"]
    assert "same turn" in assign_alias["invocationGuidance"]
    assert complete_alias["argumentBindings"] == {"actionItemId": "item_id"}
    assert "never its title" in complete_alias["invocationGuidance"]
    assert update_alias["argumentBindings"] == {"actionItemId": "item_id"}
    assert "never its title" in update_alias["invocationGuidance"]


def test_financing_summary_map_exposes_case_scoped_document_reads():
    selected = _run_immediate(server.browse_function_map(object(), "documents_research"))
    rows = {record["name"]: record for record in selected["functions"]}
    expected_reads = {
        "list_uploaded_documents",
        "search_documents",
        "search_documents_for_excerpts",
        "summarize_document",
    }

    assert expected_reads <= rows.keys()
    for name in expected_reads:
        assert rows[name]["mutatesState"] is False
    assert rows["list_uploaded_documents"]["requiresCaseId"] is True
    assert rows["summarize_document"]["trustedScopeBindings"] == {
        "case_id": "caseId",
        "app_id": "appId",
    }
    assert rows["send_uploaded_documents"]["mutatesState"] is True


def test_document_amendment_alias_navigates_to_shared_approval_workflow():
    selected = _run_immediate(server.browse_function_map(object(), "documents_research"))
    records = {item["name"]: item for item in selected["functions"]}
    amendment = records["document_amendment_request"]
    generation = records["document_generation_request"]

    assert amendment["mcpCallable"] is False
    assert amendment["metadataStatus"] == "chat_proxy_orchestration"
    assert amendment["requiresExplicitConfirmation"] is True
    assert amendment["orchestrationCategoryIds"] == ["company_operations", "contracts_workflows"]
    assert amendment["orchestrationMutationToolNames"] == ["create_workflow_run", "manage_workflow_action"]
    assert "seriesai.document.amend" in amendment["invocationGuidance"]
    assert generation["mcpCallable"] is False
    assert generation["metadataStatus"] == "chat_proxy_orchestration"
    assert generation["requiresExplicitConfirmation"] is True
    assert generation["orchestrationCategoryIds"] == ["company_operations", "contracts_workflows"]
    assert generation["orchestrationMutationToolNames"] == ["create_workflow_run", "manage_workflow_action"]
    assert "seriesai.cap_table.issuance.documents.generate" in generation["invocationGuidance"]


def test_case_signature_map_entry_is_read_only_and_uses_trusted_scope():
    selected = _run_immediate(server.browse_function_map(object(), "signatures_approvals"))
    record = next(item for item in selected["functions"] if item["name"] == "list_case_signatures")

    assert record["mcpCallable"] is True
    assert record["mutatesState"] is False
    assert record["requiresCaseId"] is True
    assert record["trustedScopeBindings"] == {"case_id": "caseId", "app_id": "appId"}
    assert "open_only should be true" in record["invocationGuidance"]
    assert "open_only" in record["inputSchema"]["properties"]
    assert record["callerResultProjection"]["targetKey"] == "slackSignatureRequests"


def test_cap_table_legacy_mutations_are_callable_map_entries_with_approval_metadata():
    selected = _run_immediate(server.browse_function_map(object(), "company_operations"))
    records = {record["name"]: record for record in selected["functions"]}

    for name in ("add_cap_table_entry", "update_cap_table_entry"):
        record = records[name]
        assert record["mcpCallable"] is True
        assert record["mutatesState"] is True
        assert record["requiresExplicitConfirmation"] is True
        assert record["appIds"] == ["3", "4"]
        assert "caseId" in record["inputSchema"].get("properties", {})
        assert "appId" in record["inputSchema"].get("properties", {})
        assert "durable workflow run" in record["invocationGuidance"]


def test_cap_table_legacy_mutation_wrappers_propose_through_durable_workflow(monkeypatch):
    calls = []

    async def fake_post(ctx, path, body):
        calls.append((ctx, path, body))
        if path == "/api/core/workflows/runs":
            return {"runId": f"run-{len(calls)}"}
        return {"proposalId": f"proposal-{len(calls)}", "status": "proposed"}

    monkeypatch.setattr(server, "_post", fake_post)
    ctx = object()
    add_result = _run_immediate(server.add_cap_table_entry(ctx, "company-1", "4", {"holderName": "Ada"}))
    update_result = _run_immediate(server.update_cap_table_entry(ctx, "company-1", "4", "entry-1", {"shares": 100}))

    assert add_result == {"runId": "run-1", "proposalId": "proposal-2", "status": "proposed"}
    assert update_result == {"runId": "run-3", "proposalId": "proposal-4", "status": "proposed"}
    assert calls[1][2]["actionType"] == "seriesai.cap_table.legacy_entry.add"
    assert calls[1][2]["requiresApproval"] is True
    assert calls[1][2]["parameters"]["capTableEntry"]["holderName"] == "Ada"
    assert calls[1][2]["parameters"]["capTableEntry"]["capTableEntryId"].startswith("cap_")
    assert calls[3][2]["actionType"] == "seriesai.cap_table.legacy_entry.update"
    assert calls[3][2]["parameters"] == {"capTableEntryId": "entry-1", "capTableEntry": {"shares": 100}}


def test_workflow_mcp_tools_use_authenticated_shared_preview_approval_api(monkeypatch):
    calls = []

    async def fake_post(ctx, path, body):
        calls.append((ctx, path, body))
        return {"runId": "run-1", "eventSink": {"token": "secret"}}

    monkeypatch.setattr(server, "_post", fake_post)
    ctx = object()
    created = _run_immediate(
        server.create_workflow_run(
            ctx,
            workflow_type="company.document_generation",
            scope={"scopeType": "company", "companyId": "company-1", "appId": "4"},
            input_data={"templateId": "safe"},
            idempotency_key="run-key-1",
        )
    )
    assert created == {"runId": "run-1"}

    proposed = _run_immediate(
        server.manage_workflow_action(
            ctx,
            run_id="run/1",
            operation="propose",
            action_type="seriesai.document.generate",
            target={"organizationId": "company-1", "appId": "4"},
            parameters={"templateId": "safe"},
        )
    )
    assert proposed["runId"] == "run-1"
    assert calls[0][1] == "/api/core/workflows/runs"
    assert calls[0][2]["scope"]["companyId"] == "company-1"
    assert calls[1][1] == "/api/core/workflows/runs/run%2F1/actions"
    assert calls[1][2]["requiresApproval"] is True
    assert calls[1][2]["operation"] == "propose"

    with pytest.raises(ValueError, match="trusted explicit user approval"):
        _run_immediate(server.manage_workflow_action(ctx, run_id="run-1", operation="approve"))
    _run_immediate(server.manage_workflow_action(ctx, run_id="run-1", operation="approve", approved=True))
    assert calls[2][2]["approved"] is True
    assert calls[2][2]["operation"] == "approve"

    with pytest.raises(ValueError, match="scope must identify"):
        _run_immediate(server.create_workflow_run(ctx, "workflow.test", {"appId": "4"}))
    with pytest.raises(ValueError, match="operation must be"):
        _run_immediate(server.manage_workflow_action(ctx, run_id="run-1", operation="approve-all"))


@pytest.mark.parametrize("action_policy", ["read_only", "task_execution"])
def test_propose_automation_carries_explicit_action_policy(monkeypatch, action_policy):
    calls = []

    async def fake_post(ctx, path, body):
        calls.append((ctx, path, body))
        return {"automation": {"actionPolicy": body["actionPolicy"]}}

    monkeypatch.setattr(server, "_post", fake_post)
    ctx = object()
    result = _run_immediate(
        server.propose_automation(
            ctx,
            name="Work through my tasks",
            cadence={"frequency": "weekly", "hour": 8, "minute": 0, "daysOfWeek": [0]},
            prompt="Complete actionable tasks assigned to me.",
            app_id="2",
            action_policy=action_policy,
        )
    )

    assert calls[0][1] == "/api/core/automations"
    assert calls[0][2]["actionPolicy"] == action_policy
    assert result["automation"]["actionPolicy"] == action_policy
    tool = next(item for item in server.mcp._tool_manager.list_tools() if item.name == "propose_automation")
    assert tool.parameters["properties"]["action_policy"]["default"] == "read_only"


def test_workflow_mcp_tools_are_indexed_as_approval_gated_capabilities():
    index = _run_immediate(server.browse_function_map(object()))
    registered_tools = server.mcp._tool_manager.list_tools()
    workflow_category = next(category for category in index["categories"] if category["id"] == "contracts_workflows")
    selected = _run_immediate(server.browse_function_map(object(), "contracts_workflows"))
    records = {record["name"]: record for record in selected["functions"]}

    assert workflow_category["count"] > 0
    assert records["create_workflow_run"]["mcpCallable"] is True
    assert records["manage_workflow_action"]["requiresExplicitConfirmation"] is True
    assert records["manage_workflow_action"]["trustedConfirmationActions"] == [
        "generate_safe",
        "generate_financing_doc",
        "generate_board_consent",
        "cap_table_issuance_intent",
        "safe_review_approval",
        "signature_approval",
        "seriesai.cap_table.issuance.start",
        "request_signature",
        "legal.signature.create",
        "document_amendment_request",
        "seriesai.document.amend",
        "document_generation_request",
        "seriesai.cap_table.issuance.documents.generate",
        "seriesai.cap_table.workflow.decision",
        "add_cap_table_entry",
        "update_cap_table_entry",
    ]
    assert "explicit user approval" in records["manage_workflow_action"]["invocationGuidance"]

    company_records = _run_immediate(server.browse_function_map(object(), "company_operations"))["functions"]
    company_by_name = {record["name"]: record for record in company_records}
    for name in ("generate_safe", "generate_financing_doc", "generate_board_consent"):
        assert company_by_name[name]["appIds"] == ["3", "4"]
        assert company_by_name[name]["requiresCaseId"] is True
        assert company_by_name[name]["trustedScopeBindings"] == {"organizationId": "caseId", "appId": "appId"}
        assert company_by_name[name]["requiresExplicitConfirmation"] is True

    communications = _run_immediate(server.browse_function_map(object(), "communications"))
    whatsapp = next(
        record for record in communications["functions"] if record["name"] == "send_whatsapp_message"
    )
    assert "confirm" in whatsapp["requiredInputs"]
    assert whatsapp["requiresExplicitConfirmation"] is True

    case_tools = _run_immediate(server.browse_function_map(object(), "case_management"))
    registered_names = {tool.name for tool in registered_tools}
    assert {"edit_case_action_items", "bulk_edit_case_action_items"} <= registered_names
    assert "edit_company_action_items" not in registered_names
    case_tool_names = {record["name"] for record in case_tools["functions"]}
    assert {"edit_case_action_items", "bulk_edit_case_action_items"} <= case_tool_names
    assert "edit_company_action_items" not in case_tool_names
    generic_action_item = next(
        record for record in case_tools["functions"] if record["name"] == "edit_case_action_items"
    )
    assert generic_action_item["dispatchTargetsByAlias"]["edit_company_action_items"]["operation"] == (
        "edit_case_action_items"
    )
    alias_guidance = generic_action_item["dispatchTargetsByAlias"]["edit_company_action_items"]["invocationGuidance"]
    assert "operation=update" in alias_guidance
    assert "assigned_to" in alias_guidance
    assert "continue in the same turn" in alias_guidance
    assert "company workspace" in alias_guidance
    assert "assistant_excerpt" in alias_guidance
    assert "next human action" in alias_guidance
    bulk_action_tool = next(tool for tool in registered_tools if tool.name == "bulk_edit_case_action_items")
    assert "assigned_to" in bulk_action_tool.parameters.get("properties", {})
    lifecycle_tool = next(tool for tool in registered_tools if tool.name == "advance_lifecycle_stage")
    assert {"caseId", "appId", "lifecycleStage"}.issubset(
        set(lifecycle_tool.parameters.get("required", [])),
    )
    for name in (
        "get_seriesai_cap_table_state",
        "list_seriesai_document_templates",
        "list_seriesai_email_messages",
    ):
        read_tool = None
        for category_entry in index["categories"]:
            read_tool_map = _run_immediate(
                server.browse_function_map(
                    object(),
                    category_id=category_entry["id"],
                    tool_names=[name],
                ),
            )
            read_tool = next(
                (record for record in read_tool_map["functions"] if record["name"] == name),
                None,
            )
            if read_tool is not None:
                break
        assert read_tool is not None, f"{name} is missing from the function map"
        assert read_tool["mutatesState"] is False
        assert read_tool["sendsExternalMessage"] is False
        assert read_tool["sendsUserEmail"] is False
        assert read_tool["startsAsyncWorkflow"] is False
        assert read_tool["returnsInlineResult"] is True

    company_details = company_by_name["get_seriesai_company_details"]
    assert "company's profile" in company_details["mcpDescription"]
    assert company_details["callerAliases"][0]["name"] == "retrieve_company_details"
    assert company_details["callerAliases"][0]["verification"] == "verified_shared_operation_id"
    revenue_metrics = company_by_name["get_seriesai_revenue_metrics"]
    assert revenue_metrics["appIds"] == ["3", "4", "5"]
    case_management = _run_immediate(server.browse_function_map(object(), "case_management"))
    important_dates = next(
        record for record in case_management["functions"] if record["name"] == "list_case_important_dates"
    )
    timeline_alias = next(
        alias for alias in important_dates["callerAliases"] if alias["name"] == "retrieve_company_timeline"
    )
    assert timeline_alias["verification"] == "verified_contract_mapping"
    assert timeline_alias["appIds"] == ["3", "4"]
    assert timeline_alias["defaultArguments"]["sort_order"] == "desc"
    assert timeline_alias["argumentBindings"]["lifecycleStage"] == "stage"
    signature_details = _run_immediate(server.browse_function_map(object(), "signatures_approvals"))
    signature_detail = next(
        record for record in signature_details["functions"] if record["name"] == "get_signature_request_detail"
    )
    signature_status_alias = next(
        alias for alias in signature_detail["callerAliases"] if alias["name"] == "retrieve_signature_status"
    )
    assert signature_status_alias["appIds"] == ["3", "4"]
    assert signature_status_alias["requiresCaseId"] is True
    assert signature_status_alias["argumentBindings"] == {"signaturePacketId": "signature_id"}
    deadlines_alias = next(
        alias for alias in important_dates["callerAliases"] if alias["name"] == "retrieve_company_deadlines"
    )
    assert deadlines_alias["verification"] == "verified_contract_mapping"
    assert deadlines_alias["appIds"] == ["3", "4"]
    assert deadlines_alias["requiresCaseId"] is True
    assert deadlines_alias["defaultArguments"]["upcoming_only"] is True
    assert deadlines_alias["argumentBindings"]["openOnly"] == "open_only"

    scheduling = _run_immediate(server.browse_function_map(object(), "scheduling"))
    date_editor = next(record for record in scheduling["functions"] if record["name"] == "edit_case_important_dates")
    acknowledgment_alias = next(
        alias for alias in date_editor["callerAliases"] if alias["name"] == "deadline_acknowledgment"
    )
    assert acknowledgment_alias["verification"] == "verified_contract_mapping"
    assert all(record["name"] != "deadline_acknowledgment" for record in scheduling["functions"])

    integrations = _run_immediate(server.browse_function_map(object(), "integrations_admin"))
    sharepoint_drives = next(
        record for record in integrations["functions"] if record["name"] == "list_sharepoint_site_drives"
    )
    assert sharepoint_drives["inputSchemasBySurface"]["chat_proxy"]["required"] == ["siteId"]
    assert sharepoint_drives["argumentBindingsBySurface"]["chat_proxy"] == {"siteId": "site_id"}
    sharepoint_children = next(
        record for record in integrations["functions"] if record["name"] == "list_sharepoint_drive_children"
    )
    assert sharepoint_children["inputSchemasBySurface"]["chat_proxy"]["required"] == ["driveId"]
    assert sharepoint_children["argumentBindingsBySurface"]["chat_proxy"] == {
        "driveId": "drive_id",
        "itemId": "item_id",
    }

    legacy_inventory_by_category = {
        "company_operations": {
            "add_cap_table_entry",
            "update_cap_table_entry",
            "diligence_room_prepare",
            "diligence_room_query",
            "generate_founder_update",
            "generate_board_consent",
            "generate_safe",
            "generate_financing_doc",
            "cap_table_issuance_intent",
            "ownership_query",
            "round_status_query",
            "retrieve_company_marketing_automations",
            "retrieve_company_marketing_performance",
            "retrieve_company_revenue",
            "retrieve_company_sales",
            "retrieve_company_upcoming_campaigns",
            "retrieve_company_web_analytics",
            "seriesai_campaign_read_refusal",
            "summarize_financing_document",
        },
        "documents_research": {
            "request_missing_lifecycle_documents",
            "document_amendment_request",
            "document_generation_request",
            "lawsuit_generation",
            "strategic_digest",
        },
        "scheduling": {"edit_company_deadlines"},
        "communications": {
            "archive_case_email_draft",
            "delete_case_email_message",
            "delete_ingested_email",
            "draft_followups",
            "general_chatbot",
            "help",
            "retrieve_account_email_drafts",
            "retrieve_case_email_drafts",
            "retrieve_case_email_timeline",
            "retrieve_inbox_review_items",
            "update_case_email_draft",
            "update_case_email_message",
        },
        "case_management": {
            "delete_case_individual",
            "named_case_scope",
            "read_case_individual_dossier",
            "retrieve_case_individuals",
            "update_case_individual",
        },
        "signatures_approvals": {
            "safe_review_approval",
            "signature_approval",
        },
        "contracts_workflows": {"start_workflow_gap_plan"},
    }
    for category_id, expected_names in legacy_inventory_by_category.items():
        inventory_category = _run_immediate(server.browse_function_map(object(), category_id))
        inventory_records = {record["name"]: record for record in inventory_category["functions"]}
        assert expected_names <= inventory_records.keys()
        for name in expected_names:
            record = inventory_records[name]
            assert "intent_handler" in record["surfaces"]
            if record["mcpCallable"]:
                assert "mcp" in record["surfaces"]
                assert "inputSchema" in record
            else:
                assert "inputSchema" not in record
                assert "operationIds" not in record

    subgroup = workflow_category["subcategories"][0]
    selected_subgroup = _run_immediate(
        server.browse_function_map(
            object(), workflow_category["id"], subcategory_id=subgroup["id"]
        )
    )
    assert selected_subgroup["subcategory"]["id"] == subgroup["id"]
    assert len(selected_subgroup["functions"]) == subgroup["count"]
    assert all(record["subcategoryId"] == subgroup["id"] for record in selected_subgroup["functions"])

    contract_category = next(item for item in index["categories"] if item["id"] == "contracts_workflows")
    workflow_group = next(
        item for item in contract_category["subcategories"] if item["id"] == "manage_workflow"
    )
    workflow_tools = _run_immediate(
        server.browse_function_map(object(), "contracts_workflows", subcategory_id="manage_workflow")
    )
    workflow_names = {record["name"] for record in workflow_tools["functions"]}
    assert {
        "triage_in_house_request",
        "transition_in_house_request",
        "transition_in_house_obligation",
        "decide_in_house_review_finding",
    } <= workflow_names
    assert workflow_group["count"] == len(workflow_names)


def test_in_house_request_list_is_callable_and_remains_app_scoped_when_allowlisted(monkeypatch):
    selected = _run_immediate(
        server.browse_function_map(object(), "contracts_workflows", ["retrieve_in_house_requests"])
    )
    record = next(item for item in selected["functions"] if item["name"] == "retrieve_in_house_requests")
    assert record["name"] == "retrieve_in_house_requests"
    assert record["appIds"] == ("5",)
    assert {"chat_run", "chat_proxy", "mcp"}.issubset(record["surfaces"])
    assert record["mcpCallable"] is True

    obligations_result = _run_immediate(
        server.browse_function_map(object(), "contracts_workflows", ["retrieve_in_house_obligations"])
    )
    obligations = next(
        item for item in obligations_result["functions"] if item["name"] == "retrieve_in_house_obligations"
    )
    assert obligations["name"] == "retrieve_in_house_obligations"
    assert obligations["appIds"] == ("5",)
    case_id_schema = obligations["inputSchema"]["properties"]["case_id"]
    assert {"type": "string"} in case_id_schema.get("anyOf", [case_id_schema])
    assert "case_id" not in obligations["inputSchema"].get("required", [])

    for tool_name in (
        "retrieve_in_house_matter",
        "retrieve_in_house_contracts",
        "retrieve_in_house_counterparties",
        "search_in_house_knowledge",
    ):
        scoped_result = _run_immediate(
            server.browse_function_map(object(), "contracts_workflows", [tool_name])
        )
        scoped_tool = next(item for item in scoped_result["functions"] if item["name"] == tool_name)
        assert scoped_tool["name"] == tool_name
        assert scoped_tool["appIds"] == ("5",)
        assert scoped_tool["mcpCallable"] is True
    assert obligations["appIds"] == ("5",)

    captured = {}

    async def fake_get(ctx, path, params=None):
        captured.update(ctx=ctx, path=path, params=params)
        return [{"id": "request-1"}]

    monkeypatch.setattr(server, "_get", fake_get)
    ctx = object()
    assert _run_immediate(server.retrieve_in_house_requests(ctx)) == [{"id": "request-1"}]
    assert captured == {"ctx": ctx, "path": "/api/in-house/requests/", "params": None}


def test_lifecycle_stage_capability_uses_company_operations_category():
    result = server._browse_function_map([{"name": "advance_lifecycle_stage"}])

    assert [category["id"] for category in result["categories"]] == ["company_operations"]


def test_allowlisted_function_map_keeps_noncallable_orchestration_record():
    selected = _run_immediate(
        server.browse_function_map(object(), "company_operations", ["get_seriesai_company_details"])
    )
    rows = {record["name"]: record for record in selected["functions"]}

    assert rows["get_seriesai_company_details"]["mcpCallable"] is True
    assert rows["diligence_room_query"]["mcpCallable"] is False
    assert rows["diligence_room_query"]["metadataStatus"] == "chat_proxy_orchestration"
    assert "advance_lifecycle_stage" not in rows


def test_cap_table_issuance_is_navigable_as_shared_workflow_orchestration():
    selected = _run_immediate(
        server.browse_function_map(object(), "company_operations", ["cap_table_issuance_intent"])
    )
    record = next(item for item in selected["functions"] if item.get("name") == "cap_table_issuance_intent")

    assert record["appIds"] == ["3", "4"]
    assert record["mcpCallable"] is False
    assert record["metadataStatus"] == "chat_proxy_orchestration"
    assert record["requiresExplicitConfirmation"] is True
    assert "contracts_workflows" in record["orchestrationCategoryIds"]
    assert "seriesai.cap_table.issuance.start" in record["invocationGuidance"]


def test_signature_request_is_navigable_as_shared_workflow_orchestration():
    selected = _run_immediate(
        server.browse_function_map(object(), "signatures_approvals", ["request_signature"])
    )
    record = next(item for item in selected["functions"] if item.get("name") == "request_signature")

    assert record["appIds"] == ["3", "4"]
    assert record["mcpCallable"] is False
    assert record["metadataStatus"] == "chat_proxy_orchestration"
    assert record["requiresExplicitConfirmation"] is True
    assert "contracts_workflows" in record["orchestrationCategoryIds"]
    assert "legal.signature.create" in record["invocationGuidance"]
    assert "signatureRecipients to signers" in record["invocationGuidance"]


def test_legacy_issuance_decisions_are_navigable_as_shared_workflow_actions():
    selected = _run_immediate(
        server.browse_function_map(
            object(),
            "signatures_approvals",
            ["safe_review_approval", "signature_approval"],
        )
    )
    records = {item["name"]: item for item in selected["functions"]}

    for name, review_kind in (("safe_review_approval", "SAFE"), ("signature_approval", "signature")):
        record = records[name]
        assert record["appIds"] == ["3", "4"]
        assert record["mcpCallable"] is False
        assert record["metadataStatus"] == "chat_proxy_orchestration"
        assert record["requiresExplicitConfirmation"] is True
        assert "seriesai.cap_table.workflow.decision" in record["invocationGuidance"]
        assert f"reviewKind={review_kind}" in record["invocationGuidance"]
        assert "workflowId" in record["invocationGuidance"]


def test_lifecycle_classifier_is_in_documents_research_and_requires_approval(monkeypatch):
    selected = _run_immediate(server.browse_function_map(object(), "documents_research", ["classify_lifecycle_upload"]))
    record = next(item for item in selected["functions"] if item.get("name") == "classify_lifecycle_upload")
    assert record["appIds"] == ["3", "4"]
    assert record["mutatesState"] is True
    assert record["requiresExplicitConfirmation"] is True

    captured = {}

    async def fake_post(ctx, path, payload):
        captured.update(ctx=ctx, path=path, payload=payload)
        return {"status": "preview"}

    monkeypatch.setattr(server, "_post", fake_post)
    ctx = object()
    result = _run_immediate(server.classify_lifecycle_upload(
        ctx, "company-1", "4", files=[{"name": "board-consent.pdf"}],
        attachments=[{"source_attachment": {"name": "signed.pdf"}}],
        text_ready_attachments=[{"filename": "bylaws.pdf", "text": "signed text"}],
        dry_run=True, approved=False,
    ))
    assert result == {"status": "preview"}
    assert captured["path"] == "/api/classify-seriesai-lifecycle-uploads/"
    assert captured["payload"]["dryRun"] is True
    assert captured["payload"]["approved"] is False
    assert captured["payload"]["attachments"] == [{"source_attachment": {"name": "signed.pdf"}}]
    assert captured["payload"]["text_ready_attachments"] == [{"filename": "bylaws.pdf", "text": "signed text"}]


def test_in_house_category_map_preserves_required_idempotency_inputs():
    selected = _run_immediate(server.browse_function_map(object(), "contracts_workflows"))
    records = {record["name"]: record for record in selected["functions"]}

    assert records["create_in_house_request"]["requiredInputs"] == [
        "subject", "description", "idempotencyKey"
    ]
    assert records["create_in_house_request_comment"]["requiredInputs"] == [
        "request_id", "body", "idempotencyKey"
    ]
    assert records["transition_in_house_request"]["requiredInputs"] == [
        "request_id", "status", "idempotencyKey"
    ]


def test_action_item_filters_match_django_api_query_contract(monkeypatch):
    captured = {}

    async def fake_get(ctx, path, params=None):
        captured.update(path=path, params=params)
        return {"items": []}

    monkeypatch.setattr(server, "_get", fake_get)
    _run_immediate(
        server.list_action_items(
            SimpleNamespace(),
            case_id="matter-1",
            status="open",
            assignee_id="user-1",
            app_id="4",
            open_only=True,
            search="renewal",
            include_archived=False,
            parent_task_id="task-1",
            result_limit=80,
            include_lifecycle_suggestions=True,
        )
    )

    assert captured == {
        "path": "/api/action-items/",
        "params": {
            "caseId": "matter-1",
            "itemId": None,
            "status": "open",
            "assignedTo": "user-1",
            "appId": "4",
            "openOnly": True,
            "search": "renewal",
            "includeArchived": False,
            "parentTaskId": "task-1",
            "resultLimit": 80,
            "resultOffset": None,
            "includeLifecycleSuggestions": True,
        },
    }


def test_important_date_filters_match_django_api_query_contract(monkeypatch):
    captured = {}

    async def fake_get(ctx, path, params=None):
        captured.update(path=path, params=params)
        return {"items": []}

    monkeypatch.setattr(server, "_get", fake_get)
    _run_immediate(
        server.list_case_important_dates(
            SimpleNamespace(),
            case_id="company-1",
            app_id="4",
            date_from="2026-01-01",
            date_to="2026-12-31",
            event_type="lifecycle_transition",
            source_kind="company_record",
            practice_area="startup",
            stage="seed",
            exclude_external_calendar=True,
            exclude_booking_projections=True,
            require_displayable=True,
            open_only=True,
            result_limit=50,
        )
    )

    assert captured == {
        "path": "/api/case-important-dates/",
        "params": {
            "caseId": "company-1",
            "appId": "4",
            "dateFrom": "2026-01-01",
            "dateTo": "2026-12-31",
            "eventType": "lifecycle_transition",
            "sourceKind": "company_record",
            "practiceArea": "startup",
            "stage": "seed",
            "excludeExternalCalendar": True,
            "excludeBookingProjections": True,
            "requireDisplayable": True,
            "openOnly": True,
            "resultLimit": 50,
        },
    }
def test_deadline_acknowledgment_forwards_approval_to_shared_edit_api(monkeypatch):
    calls = []

    async def fake_post(ctx, path, payload):
        calls.append((ctx, path, payload))
        return payload

    monkeypatch.setattr(server, "_post", fake_post)
    ctx = object()
    result = _run_immediate(
        server.edit_case_important_dates(
            ctx,
            case_id="case-1",
            operation="acknowledge",
            deadline_id="deadline-1",
            app_id="4",
            approved=True,
        )
    )

    assert result["approved"] is True
    assert calls == [
        (
            ctx,
            "/api/edit-case-important-dates/",
            {
                "caseId": "case-1",
                "operation": "acknowledge",
                "deadlineId": "deadline-1",
                "appId": "4",
                "approved": True,
            },
        )
    ]


def test_inbox_mapping_guidance_keeps_provider_ids_internal():
    records = server.universal_function_records()
    record = next(item for item in records if item["name"] == "map_inbox_review_thread")

    assert record["requiredInputs"] == ["caseId", "confirm"]
    assert "sender, subject, or date" in record["invocationGuidance"]
    assert "keep provider IDs internal" in record["invocationGuidance"]
