const express = require('express');
const jwt = require('jsonwebtoken');
const request = require('supertest');
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

const createApp = () => {
  const app = express();
  app.use(express.json());
  app.use((req, _res, next) => {
    req.user = { id: OWNER_ID };
    next();
  });
  app.use(applyViennaMeetingRetention);
  app.post('/chat', (req, res) =>
    res.json({
      retention: req._agentEventBindingRetention ?? null,
      bodyTemporary: req.body.isTemporary ?? false,
      bodyExpiry: req.body.expiredAt ?? null,
    }),
  );
  return app;
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

  test('ignores body-only temporary and expiry claims', async () => {
    const response = await request(createApp())
      .post('/chat')
      .send({ conversationId: CONVERSATION_ID, isTemporary: true, expiredAt: NOW + 10 });
    expect(response.status).toBe(200);
    expect(response.body.retention).toBeNull();
  });

  test('applies signed owner- and conversation-bound expiry', async () => {
    const response = await request(createApp())
      .post('/chat')
      .set('X-Vienna-Meeting-Retention', createGrant())
      .send({ conversationId: CONVERSATION_ID });
    expect(response.status).toBe(200);
    expect(response.body.retention.isTemporary).toBe(true);
    expect(response.body.retention.expiredAt).toBe(new Date((NOW + 60) * 1000).toISOString());
  });

  test.each([
    ['wrong owner', { sub: 'other-owner' }],
    ['wrong conversation', { conversationId: 'other-conversation' }],
    ['wrong purpose', { purpose: 'other-purpose' }],
    ['non-temporary claim', { isTemporary: false }],
    ['expired grant', { exp: NOW - 1 }],
    ['overlong grant', { exp: NOW + 24 * 60 * 60 + 1 }],
  ])('rejects %s', async (_label, claims) => {
    const response = await request(createApp())
      .post('/chat')
      .set('X-Vienna-Meeting-Retention', createGrant(claims))
      .send({ conversationId: CONVERSATION_ID });
    expect(response.status).toBe(401);
  });

  test('rejects a grant signed with a different secret', async () => {
    const response = await request(createApp())
      .post('/chat')
      .set('X-Vienna-Meeting-Retention', createGrant({}, 'wrong-secret'))
      .send({ conversationId: CONVERSATION_ID });
    expect(response.status).toBe(401);
  });

  test('fails closed when the signing secret is unavailable', async () => {
    delete process.env.CHAT_SECRET;
    const response = await request(createApp())
      .post('/chat')
      .set('X-Vienna-Meeting-Retention', createGrant())
      .send({ conversationId: CONVERSATION_ID });
    expect(response.status).toBe(503);
  });
});
