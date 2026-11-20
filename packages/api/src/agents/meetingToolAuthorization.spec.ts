import { assertMeetingToolsAuthorized } from './meetingToolAuthorization';

const GRANT = 'signed-meeting-retention-grant';
const SESSION_ID = 'c1b61f9a-353d-4882-a739-9524d7c51a67';

const createResponse = (body: unknown, ok = true) => ({
  ok,
  status: ok ? 200 : 403,
  json: jest.fn().mockResolvedValue(body),
});

describe('assertMeetingToolsAuthorized', () => {
  it('does not make a Django request for ordinary agent turns', async () => {
    const fetcher = jest.fn();

    await expect(
      assertMeetingToolsAuthorized({ toolNames: ['search'], fetcher }),
    ).resolves.toBeUndefined();

    expect(fetcher).not.toHaveBeenCalled();
  });

  it('checks the tool batch once with the signed grant and sends no content body', async () => {
    const fetcher = jest.fn().mockResolvedValue(createResponse({ authorized: true }));
    const signal = AbortSignal.abort();

    await expect(
      assertMeetingToolsAuthorized({
        baseUrl: 'https://api.example.test',
        retentionGrant: GRANT,
        sessionId: SESSION_ID,
        toolNames: ['search', 'calendar'],
        fetcher,
        signalFactory: () => signal,
      }),
    ).resolves.toBeUndefined();

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledWith(
      new URL(
        `/api/core/meeting-sessions/internal/${SESSION_ID}/agent-tool-authorization/`,
        'https://api.example.test',
      ),
      {
        method: 'POST',
        headers: { 'X-Vienna-Meeting-Retention': GRANT },
        signal,
      },
    );
    expect(fetcher.mock.calls[0][1]).not.toHaveProperty('body');
  });

  it('fails closed when Django denies or cannot confirm authorization', async () => {
    await expect(
      assertMeetingToolsAuthorized({
        baseUrl: 'https://api.example.test',
        retentionGrant: GRANT,
        sessionId: SESSION_ID,
        toolNames: ['search'],
        fetcher: jest.fn().mockResolvedValue(createResponse({ authorized: false })),
      }),
    ).rejects.toThrow('Vienna meeting tool authorization was denied');

    await expect(
      assertMeetingToolsAuthorized({
        baseUrl: 'https://api.example.test',
        retentionGrant: GRANT,
        sessionId: SESSION_ID,
        toolNames: ['search'],
        fetcher: jest.fn().mockRejectedValue(new Error('private network detail')),
      }),
    ).rejects.toThrow('Vienna meeting tool authorization is unavailable');
  });

  it('fails closed when a meeting grant has no configured Django base URL', async () => {
    await expect(
      assertMeetingToolsAuthorized({
        retentionGrant: GRANT,
        sessionId: SESSION_ID,
        toolNames: ['search'],
        fetcher: jest.fn(),
      }),
    ).rejects.toThrow('Vienna meeting tool authorization is unavailable');
  });
});
