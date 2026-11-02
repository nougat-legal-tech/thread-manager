const jwt = require('jsonwebtoken');

const ISSUER = 'juristai-meeting';
const AUDIENCE = 'librechat-agent-turn';
const RETENTION_PURPOSE = 'temporary_meeting_transcript';
const MAX_GRANT_AGE_SECONDS = 24 * 60 * 60;

const getUserId = (user) => String(user?.id ?? user?._id ?? '');

const getConversationId = (request) => {
  const body = request.body ?? {};
  return String(
    body.conversationId ??
      body.conversation_id ??
      body.arg?.conversationId ??
      body.arg?.conversation_id ??
      '',
  );
};

/**
 * Applies meeting-only retention metadata from a signed Django assertion.
 * Body-provided isTemporary/expiredAt values are never treated as proof.
 */
function applyViennaMeetingRetention(request, response, next) {
  const token = request.get('x-vienna-meeting-retention');
  if (!token) {
    const body = request.body ?? {};
    const isMeetingRequest =
      body.channel === 'meeting' ||
      body.sourceChannel === 'meeting' ||
      body.metadata?.source === 'vienna_meeting';
    if (isMeetingRequest) {
      return response.status(401).json({ error: 'meeting_retention_grant_required' });
    }
    return next();
  }

  const secret = process.env.CHAT_SECRET;
  if (!secret) {
    return response.status(503).json({ error: 'meeting_retention_unavailable' });
  }

  let claims;
  try {
    claims = jwt.verify(token, secret, {
      algorithms: ['HS256'],
      audience: AUDIENCE,
      issuer: ISSUER,
    });
  } catch (_error) {
    return response.status(401).json({ error: 'invalid_meeting_retention_grant' });
  }

  const userId = getUserId(request.user);
  const conversationId = getConversationId(request);
  const issuedAt = Number(claims.iat);
  const expiresAt = Number(claims.exp);
  const now = Math.floor(Date.now() / 1000);
  if (
    claims.purpose !== RETENTION_PURPOSE ||
    claims.isTemporary !== true ||
    !claims.sub ||
    String(claims.sub) !== userId ||
    !claims.conversationId ||
    String(claims.conversationId) !== conversationId ||
    !Number.isInteger(issuedAt) ||
    !Number.isInteger(expiresAt) ||
    issuedAt > now + 30 ||
    expiresAt <= now ||
    expiresAt <= issuedAt ||
    expiresAt - issuedAt > MAX_GRANT_AGE_SECONDS
  ) {
    return response.status(401).json({ error: 'invalid_meeting_retention_binding' });
  }

  request._agentEventBindingRetention = {
    isTemporary: true,
    expiredAt: new Date(expiresAt * 1000),
    suppressLangfuseTracing: true,
  };
  return next();
}

module.exports = applyViennaMeetingRetention;
module.exports.MAX_GRANT_AGE_SECONDS = MAX_GRANT_AGE_SECONDS;
