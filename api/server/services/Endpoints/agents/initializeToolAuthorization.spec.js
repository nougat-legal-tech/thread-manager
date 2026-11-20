const { loadToolsAfterMeetingAuthorization } = require('./meetingToolLoadBoundary');

describe('loadToolsAfterMeetingAuthorization', () => {
  it('loads tools only after the current meeting policy check completes', async () => {
    const events = [];
    const authorize = jest.fn(async () => {
      events.push('authorized');
    });
    const loadTools = jest.fn(async () => {
      events.push('loaded');
      return { loadedTools: ['web'] };
    });

    await expect(loadToolsAfterMeetingAuthorization(authorize, loadTools)).resolves.toEqual({
      loadedTools: ['web'],
    });

    expect(events).toEqual(['authorized', 'loaded']);
    expect(authorize).toHaveBeenCalledTimes(1);
    expect(loadTools).toHaveBeenCalledTimes(1);
  });

  it('fails closed without invoking the tool loader when authorization rejects', async () => {
    const authorize = jest.fn().mockRejectedValue(new Error('meeting authorization denied'));
    const loadTools = jest.fn();

    await expect(loadToolsAfterMeetingAuthorization(authorize, loadTools)).rejects.toThrow(
      'meeting authorization denied',
    );

    expect(authorize).toHaveBeenCalledTimes(1);
    expect(loadTools).not.toHaveBeenCalled();
  });
});
