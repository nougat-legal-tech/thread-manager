const { loadToolsAfterMeetingAuthorization } = require('./meetingToolLoadBoundary');

describe('loadToolsAfterMeetingAuthorization', () => {
  it('authorizes the meeting before resolving tools', async () => {
    const order = [];
    const loadTools = jest.fn(async () => {
      order.push('loaded');
      return ['meeting-tool'];
    });

    const result = await loadToolsAfterMeetingAuthorization(async () => {
      order.push('authorized');
    }, loadTools);

    expect(order).toEqual(['authorized', 'loaded']);
    expect(loadTools).toHaveBeenCalledTimes(1);
    expect(result).toEqual(['meeting-tool']);
  });

  it('does not load tools when meeting authorization is denied', async () => {
    const loadTools = jest.fn();
    const error = new Error('meeting authorization denied');

    await expect(
      loadToolsAfterMeetingAuthorization(async () => {
        throw error;
      }, loadTools),
    ).rejects.toBe(error);

    expect(loadTools).not.toHaveBeenCalled();
  });
});
