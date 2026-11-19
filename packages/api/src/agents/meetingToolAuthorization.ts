export type MeetingToolAuthorizationResponse = {
  ok: boolean;
  status: number;
  json: () => Promise<unknown>;
};

export type MeetingToolAuthorizationFetch = (
  url: URL,
  init: { method: 'POST'; headers: Record<string, string>; signal: AbortSignal },
) => Promise<MeetingToolAuthorizationResponse>;

export type AssertMeetingToolsAuthorizedOptions = {
  baseUrl?: string;
  retentionGrant?: string;
  sessionId?: string;
  toolNames: string[];
  fetcher: MeetingToolAuthorizationFetch;
  signalFactory?: () => AbortSignal;
};

const createTimeoutSignal = () => AbortSignal.timeout(2_000);

/**
 * Recheck Django's session authority immediately before loading tools for a
 * meeting turn. The grant and session ID are server-side request data; no
 * transcript or tool arguments cross this policy boundary.
 */
export async function assertMeetingToolsAuthorized({
  baseUrl,
  retentionGrant,
  sessionId,
  toolNames,
  fetcher,
  signalFactory = createTimeoutSignal,
}: AssertMeetingToolsAuthorizedOptions): Promise<void> {
  if (!retentionGrant || toolNames.length === 0) {
    return;
  }

  if (!sessionId || !baseUrl) {
    throw new Error('Vienna meeting tool authorization is unavailable');
  }

  let url: URL;
  try {
    url = new URL(
      `/api/core/meeting-sessions/internal/${encodeURIComponent(sessionId)}/agent-tool-authorization/`,
      baseUrl,
    );
  } catch (_error) {
    throw new Error('Vienna meeting tool authorization is unavailable');
  }

  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error('Vienna meeting tool authorization is unavailable');
  }

  const checks = toolNames.map(async () => {
    let response: MeetingToolAuthorizationResponse;
    try {
      response = await fetcher(url, {
        method: 'POST',
        headers: { 'X-Vienna-Meeting-Retention': retentionGrant },
        signal: signalFactory(),
      });
    } catch (_error) {
      throw new Error('Vienna meeting tool authorization is unavailable');
    }

    if (!response.ok) {
      throw new Error('Vienna meeting tool authorization was denied');
    }

    let result: unknown;
    try {
      result = await response.json();
    } catch (_error) {
      throw new Error('Vienna meeting tool authorization was denied');
    }

    if (
      typeof result !== 'object' ||
      result === null ||
      !('authorized' in result) ||
      result.authorized !== true
    ) {
      throw new Error('Vienna meeting tool authorization was denied');
    }
  });

  await Promise.all(checks);
}
