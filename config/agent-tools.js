function buildAgentTools({
  existingTools,
  actionTools,
  encodedDomain,
  legacyDomain,
  actionId,
  meetingToolKey,
}) {
  const preservedTools = (Array.isArray(existingTools) ? existingTools : []).filter(
    (tool) =>
      !(
        tool &&
        (tool === meetingToolKey ||
          tool.includes(encodedDomain) ||
          tool.includes(legacyDomain) ||
          tool.includes(actionId))
      ),
  );
  return [...new Set([...preservedTools, ...actionTools, meetingToolKey])];
}

module.exports = { buildAgentTools };
