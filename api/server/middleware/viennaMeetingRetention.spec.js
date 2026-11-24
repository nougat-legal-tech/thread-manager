const jwt = require('jsonwebtoken');
const applyViennaMeetingRetention = require('./viennaMeetingRetention');

const SECRET = 'vienna-meeting-retention-test-secret';
const OWNER_ID = '0123456789abcdef01234567';
const DJANGO_OWNER_ID = 'owner-123';
const ACTOR_USER_ID = 'cognito-user-123';
const CONVERSATION_ID = 'meeting-123';
const TURN_ID = 'a'.repeat(64);
const TRANSCRIPT_REVISION = 'b'.repeat(64);
const NOW = 1_800_000_000;

const createGrant = (overrides = {}, secret = SECRET) =>
  jwt.sign(
    {
      iss: 'vienna-meeting',
      aud: 'vienna-chatbot',
      sub: OWNER_ID,
      djangoUserId: DJANGO_OWNER_ID,
      actorUserId: ACTOR_USER_ID,
      conversationId: CONVERSATION_ID,
      sessionId: CONVERSATION_ID,
      turnId: TURN_ID,
      transcriptRevision: TRANSCRIPT_REVISION,
      approvalActionId: 'approval-42',
      appId: '2',
      caseId: 'case-123',
      capabilities: ['agent_turn'],
      grantVersion: 1,
      purpose: 'meeting_turn',
      retentionPurpose: 'temporary_meeting_transcript',
      isTemporary: true,
      retentionExpiresAt: NOW + 24 * 60 * 60,
      iat: NOW,
      exp: NOW + 60,
      ...overrides,
    },
    secret,
    { algorithm: 'HS256' },
  );

const invokeRetention = ({
  token,
  body = {},
  userId = OWNER_ID,
  config,
  path = '/',
  requestId = 'meeting-request-1',
} = {}) => {
  const req = {
    body,
    path,
    user: { id: userId },
    config,
    get: jest.fn((headerName) =>
      headerName.toLowerCase() === 'x-vienna-meeting-retention'
        ? token
        : headerName.toLowerCase() === 'x-request-id'
          ? requestId
          : undefined,
    ),
  };
  const res = {
    statusCode: 200,
    body: undefined,
    status: jest.fn(function setStatus(statusCode) {
      this.statusCode = statusCode;
      return this;
    }),
    json: jest.fn(function setBody(body) {
      this.body = body;
      return this;
    }),
  };
  const next = jest.fn();
  applyViennaMeetingRetention(req, res, next);
  return { req, res, next };
};

const meetingBody = (overrides = {}) => ({
  channel: 'meeting',
  conversationId: CONVERSATION_ID,
  appId: 2,
  caseId: 'case-123',
  sessionKey: `meeting:${CONVERSATION_ID}`,
  idempotencyKey: `meeting:${TURN_ID}`,
  userId: ACTOR_USER_ID,
  metadata: {
    meetingSessionId: CONVERSATION_ID,
    meetingTurnId: TURN_ID,
    meetingTranscriptRevision: TRANSCRIPT_REVISION,
    source: 'vienna_meeting',
    architectureTraceId: 'meeting-request-1',
    userId: ACTOR_USER_ID,
  },
  ...overrides,
});

describe('Vienna meeting retention grant', () => {
  let previousChatSecret;

  beforeEach(() => {
    previousChatSecret = process.env.CHAT_SECRET;
    process.env.CHAT_SECRET = SECRET;
    jest.spyOn(Date, 'now').mockReturnValue(NOW * 1000);
  });

  afterEach(() => {
    jest.restoreAllMocks();
    if (previousChatSecret === undefined) {
      delete process.env.CHAT_SECRET;
    } else {
      process.env.CHAT_SECRET = previousChatSecret;
    }
  });

  test('ignores body-only temporary and expiry claims', () => {
    const { req, next, res } = invokeRetention({
      body: { conversationId: CONVERSATION_ID, isTemporary: true, expiredAt: NOW + 10 },
    });
    expect(next).toHaveBeenCalledTimes(1);
    expect(req._agentEventBindingRetention).toBeUndefined();
    expect(res.status).not.toHaveBeenCalled();
  });

  test('requires the signed retention grant for Vienna meeting turns', () => {
    const { next, res } = invokeRetention({
      body: {
        channel: 'meeting',
        conversationId: CONVERSATION_ID,
        isTemporary: true,
        expiredAt: NOW + 10,
      },
    });
    expect(next).not.toHaveBeenCalled();
    expect(res.statusCode).toBe(401);
    expect(res.body).toEqual({ error: 'meeting_retention_grant_required' });
  });

  test('applies signed owner- and conversation-bound expiry', () => {
    const { req, next } = invokeRetention({
      token: createGrant(),
      body: meetingBody(),
    });
    expect(next).toHaveBeenCalledTimes(1);
    expect(req._agentEventBindingRetention).toEqual({
      isTemporary: true,
      expiredAt: new Date((NOW + 24 * 60 * 60) * 1000),
      suppressLangfuseTracing: true,
    });
  });

  test('accepts the normalized Django meeting payload with its authorized tool context', () => {
    const requestId = 'meeting-trace-1';
    const body = {
      ...meetingBody(),
      input: 'Vienna, can you research this?',
      text: 'Vienna, can you research this?',
      runId: 'run-123',
      run_id: 'run-123',
      conversation_id: CONVERSATION_ID,
      idempotency_key: `meeting:${TURN_ID}`,
      session_key: `meeting:${CONVERSATION_ID}`,
      userId: ACTOR_USER_ID,
      agent_id: 'agent-vienna',
      model_parameters: {},
      additionalModelRequestFields: {},
      instructions: 'Use Vienna tools for direct questions.',
      juristaiToolContext: {
        user: { userId: ACTOR_USER_ID, appId: '2' },
        availableTools: [{ name: 'search_documents', categoryId: 'documents_research' }],
      },
      metadata: {
        ...meetingBody().metadata,
        architectureTraceId: requestId,
        userId: ACTOR_USER_ID,
      },
    };
    const { req, next, res } = invokeRetention({
      token: createGrant(),
      body,
      path: '/meeting-turn',
      requestId,
    });

    expect(next).toHaveBeenCalledTimes(1);
    expect(res.status).not.toHaveBeenCalled();
    expect(req._agentEventBindingRetention.isTemporary).toBe(true);
  });

  test('rejects a normalized meeting payload whose authenticated actor differs from the signed grant', () => {
    const { next, res } = invokeRetention({
      token: createGrant(),
      body: meetingBody({ userId: 'different-actor' }),
    });

    expect(next).not.toHaveBeenCalled();
    expect(res.statusCode).toBe(401);
    expect(res.body).toEqual({ error: 'invalid_meeting_retention_binding' });
  });

  test('accepts an approval continuation only for its signed action and session', () => {
    const { req, next, res } = invokeRetention({
      token: createGrant(),
      path: '/resume',
      body: { conversationId: CONVERSATION_ID, actionId: 'approval-42', decisions: [] },
    });
    expect(next).toHaveBeenCalledTimes(1);
    expect(res.status).not.toHaveBeenCalled();
    expect(req._agentEventBindingRetention.isTemporary).toBe(true);
  });

  test('rejects an approval continuation for a different pending action', () => {
    const { next, res } = invokeRetention({
      token: createGrant(),
      path: '/resume',
      body: { conversationId: CONVERSATION_ID, actionId: 'approval-other', decisions: [] },
    });
    expect(next).not.toHaveBeenCalled();
    expect(res.statusCode).toBe(401);
  });

  test('uses an isolated bounded checkpoint store without mutating shared config', () => {
    const config = {
      endpoints: {
        agents: {
          model: 'vienna',
          checkpointer: { type: 'memory', ttl: 60, checkpointCollectionName: 'shared' },
        },
      },
    };
    const { req, next } = invokeRetention({
      token: createGrant(),
      body: meetingBody(),
      config,
    });

    expect(next).toHaveBeenCalledTimes(1);
    expect(req.config).not.toBe(config);
    expect(req.config.endpoints.agents).toEqual({
      model: 'vienna',
      checkpointer: {
        type: 'mongo',
        ttl: 24 * 60 * 60,
        checkpointCollectionName: 'vienna_meeting_agent_checkpoints',
        checkpointWritesCollectionName: 'vienna_meeting_agent_checkpoint_writes',
      },
    });
    expect(config.endpoints.agents.checkpointer).toEqual({
      type: 'memory',
      ttl: 60,
      checkpointCollectionName: 'shared',
    });
  });

  test.each([
    ['wrong owner', { sub: 'other-owner' }],
    ['missing Django owner binding', { djangoUserId: undefined }],
    ['wrong conversation', { conversationId: 'other-conversation' }],
    ['wrong session', { sessionId: 'other-session' }],
    ['wrong purpose', { purpose: 'other-purpose' }],
    ['wrong version', { grantVersion: 2 }],
    ['wrong capability', { capabilities: ['generic_chat'] }],
    ['non-temporary claim', { isTemporary: false }],
    ['expired grant', { exp: NOW - 1 }],
    ['overlong grant', { exp: NOW + 5 * 60 + 1 }],
    ['missing retention expiry', { retentionExpiresAt: undefined }],
    ['expired retention', { retentionExpiresAt: NOW - 1 }],
    ['overlong retention', { retentionExpiresAt: NOW + 24 * 60 * 60 + 1 }],
    ['missing transcript revision', { transcriptRevision: undefined }],
    ['malformed transcript revision', { transcriptRevision: 'not-a-revision' }],
  ])('rejects %s', (_label, claims) => {
    const { next, res } = invokeRetention({
      token: createGrant(claims),
      body: meetingBody(),
    });
    expect(next).not.toHaveBeenCalled();
    expect(res.statusCode).toBe(401);
  });

  test.each([
    ['wrong turn', { metadata: { ...meetingBody().metadata, meetingTurnId: 'b'.repeat(64) } }],
    [
      'wrong transcript revision',
      { metadata: { ...meetingBody().metadata, meetingTranscriptRevision: 'c'.repeat(64) } },
    ],
    ['wrong app', { appId: 3 }],
    ['wrong case', { caseId: 'other-case' }],
    ['wrong session key', { sessionKey: 'meeting:other-session' }],
    ['wrong idempotency key', { idempotencyKey: 'meeting:other-turn' }],
    ['missing meeting metadata', { metadata: {} }],
  ])('rejects request with %s', (_label, override) => {
    const { next, res } = invokeRetention({
      token: createGrant(),
      body:
        override.metadata != null
          ? { ...meetingBody(), metadata: override.metadata }
          : { ...meetingBody(), ...override },
    });
    expect(next).not.toHaveBeenCalled();
    expect(res.statusCode).toBe(401);
  });

  test('rejects caller-selected agent, model, or tool fields on a meeting turn', () => {
    const { next, res } = invokeRetention({
      token: createGrant(),
      body: { ...meetingBody(), model: 'caller-model' },
    });
    expect(next).not.toHaveBeenCalled();
    expect(res.statusCode).toBe(401);
  });

  test('rejects a grant signed with a different secret', () => {
    const { next, res } = invokeRetention({
      token: createGrant({}, 'wrong-secret'),
      body: { conversationId: CONVERSATION_ID },
    });
    expect(next).not.toHaveBeenCalled();
    expect(res.statusCode).toBe(401);
    expect(res.body).toEqual({ error: 'invalid_meeting_retention_grant' });
  });

  test('fails closed when the signing secret is unavailable', () => {
    delete process.env.CHAT_SECRET;
    const { next, res } = invokeRetention({
      token: createGrant(),
      body: { conversationId: CONVERSATION_ID },
    });
    expect(next).not.toHaveBeenCalled();
    expect(res.statusCode).toBe(503);
    expect(res.body).toEqual({ error: 'meeting_retention_unavailable' });
  });
});
