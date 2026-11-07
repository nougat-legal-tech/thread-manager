const assert = require('node:assert/strict');
const { test } = require('node:test');
const { buildAgentTools } = require('./agent-tools');

test('meeting tool attachment preserves unrelated tools, replaces stale Action tools, and is idempotent', () => {
  const options = {
    existingTools: [
      'execute_code',
      'search_case_mcp_existing-server',
      'old_case_tool_action_api_dev_juristai_org',
    ],
    actionTools: ['get_case_action_api_dev_juristai_org'],
    encodedDomain: 'api_dev_juristai_org',
    legacyDomain: 'api_dev_juristai_org',
    actionId: 'old-action-id',
    meetingToolKey: 'join_meeting_mcp_juristai-django',
  };
  const firstBinding = buildAgentTools(options);
  const secondBinding = buildAgentTools({ ...options, existingTools: firstBinding });

  assert.deepEqual(firstBinding, [
    'execute_code',
    'search_case_mcp_existing-server',
    ...options.actionTools,
    options.meetingToolKey,
  ]);
  assert.deepEqual(secondBinding, firstBinding);
});
