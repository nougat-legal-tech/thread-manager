const jwt = require('jsonwebtoken');
const applyViennaMeetingRetention = require('./viennaMeetingRetention');

const SECRET = 'vienna-meeting-retention-test-secret';
const OWNER_ID = 'owner-123';
const CONVERSATION_ID = 'meeting-123';
const NOW = 1_800_000_000;

const createGrant = (overrides = {}, secret = SECRET) =>
  jwt.sign(
    {
      iss: 'juristai-meeting',
      aud: 'librechat-agent-turn',
      sub: OWNER_ID,
      conversationId: CONVERSATION_ID,
      purpose: 'temporary_meeting_transcript',
      isTemporary: true,
      iat: NOW,
      exp: NOW + 60,
      ...overrides,
    },
    secret,
    { algorithm: 'HS256' },
  );

const invokeRetention = ({ token, body = {}, userId = OWNER_ID } = {}) => {
  const req = {
    body,
    user: { id: userId },
    get: jest.fn((headerName) =>
      headerName.toLowerCase() === 'x-vienna-meeting-retention' ? token : undefined,
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
      body: { conversationId: CONVERSATION_ID },
    });
    expect(next).toHaveBeenCalledTimes(1);
    expect(req._agentEventBindingRetention).toEqual({
      isTemporary: true,
      expiredAt: new Date((NOW + 60) * 1000),
      suppressLangfuseTracing: true,
    });
  });

  test.each([
    ['wrong owner', { sub: 'other-owner' }],
    ['wrong conversation', { conversationId: 'other-conversation' }],
    ['wrong purpose', { purpose: 'other-purpose' }],
    ['non-temporary claim', { isTemporary: false }],
    ['expired grant', { exp: NOW - 1 }],
    ['overlong grant', { exp: NOW + 24 * 60 * 60 + 1 }],
  ])('rejects %s', (_label, claims) => {
    const { next, res } = invokeRetention({
      token: createGrant(claims),
      body: { conversationId: CONVERSATION_ID },
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
