const jwt = require('jsonwebtoken');

const ISSUER = 'vienna-meeting';
const AUDIENCE = 'vienna-chatbot';
const PURPOSE = 'meeting_turn';
const RETENTION_PURPOSE = 'temporary_meeting_transcript';
const MAX_GRANT_AGE_SECONDS = 5 * 60;
const MAX_RETENTION_SECONDS = 24 * 60 * 60;
const MEETING_TURN_BODY_FIELDS = new Set([
  'input', 'conversationId', 'runId', 'channel', 'sessionKey', 'idempotencyKey',
  'isTemporary', 'appId', 'caseId', 'metadata', 'stream',
]);
const MEETING_RESUME_BODY_FIELDS = new Set(['conversationId', 'actionId', 'agent_id', 'endpoint', 'decisions']);
const MEETING_METADATA_FIELDS = new Set([
  'meetingSessionId', 'meetingTurnId', 'meetingTranscriptRevision', 'source',
]);
const MEETING_CHECKPOINT_COLLECTION = 'vienna_meeting_agent_checkpoints';
const MEETING_CHECKPOINT_WRITES_COLLECTION = 'vienna_meeting_agent_checkpoint_writes';

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
  const body = request.body ?? {};
  const metadata = body.metadata ?? {};
  const conversationId = getConversationId(request);
  const appId = String(body.appId ?? '');
  const caseId = body.caseId == null ? null : String(body.caseId);
  const claimedCaseId = claims.caseId == null ? null : String(claims.caseId);
  const turnId = String(claims.turnId ?? '');
  const transcriptRevision = String(claims.transcriptRevision ?? '');
  const approvalActionId = String(claims.approvalActionId ?? '');
  const isResumeRequest = request.path === '/resume';
  const allowedBodyFields = isResumeRequest ? MEETING_RESUME_BODY_FIELDS : MEETING_TURN_BODY_FIELDS;
  const hasUnexpectedBodyField = Object.keys(body).some((key) => !allowedBodyFields.has(key));
  const hasUnexpectedMetadataField = !isResumeRequest && Object.keys(metadata).some(
    (key) => !MEETING_METADATA_FIELDS.has(key),
  );
  const issuedAt = Number(claims.iat);
  const expiresAt = Number(claims.exp);
  const retentionExpiresAt = Number(claims.retentionExpiresAt);
  const now = Math.floor(Date.now() / 1000);
  if (
    claims.purpose !== PURPOSE ||
    claims.retentionPurpose !== RETENTION_PURPOSE ||
    claims.isTemporary !== true ||
    claims.grantVersion !== 1 ||
    !Array.isArray(claims.capabilities) ||
    claims.capabilities.length !== 1 ||
    claims.capabilities[0] !== 'agent_turn' ||
    !claims.sub ||
    String(claims.sub) !== userId ||
    !claims.djangoUserId ||
    !claims.conversationId ||
    String(claims.conversationId) !== conversationId ||
    String(claims.sessionId ?? '') !== conversationId ||
    hasUnexpectedBodyField ||
    hasUnexpectedMetadataField ||
    !/^[0-9a-f]{64}$/.test(turnId) ||
    (!isResumeRequest && body.channel !== 'meeting') ||
    (!isResumeRequest && metadata.source !== 'vienna_meeting') ||
    (!isResumeRequest && String(metadata.meetingSessionId ?? '') !== conversationId) ||
    (!isResumeRequest && String(metadata.meetingTurnId ?? '') !== turnId) ||
    (!isResumeRequest && !/^[0-9a-f]{64}$/.test(transcriptRevision)) ||
    (!isResumeRequest && String(metadata.meetingTranscriptRevision ?? '') !== transcriptRevision) ||
    (!isResumeRequest && String(body.sessionKey ?? body.session_key ?? '') !== `meeting:${conversationId}`) ||
    (!isResumeRequest && String(body.idempotencyKey ?? body.idempotency_key ?? '') !== `meeting:${turnId}`) ||
    (isResumeRequest && (!/^[A-Za-z0-9._:-]{1,128}$/.test(approvalActionId) || String(body.actionId ?? '') !== approvalActionId)) ||
    !claims.appId ||
    (!isResumeRequest && String(claims.appId) !== appId) ||
    (!isResumeRequest && claimedCaseId !== caseId) ||
    !Number.isInteger(issuedAt) ||
    !Number.isInteger(expiresAt) ||
    !Number.isInteger(retentionExpiresAt) ||
    issuedAt > now + 30 ||
    expiresAt <= now ||
    expiresAt <= issuedAt ||
    expiresAt - issuedAt > MAX_GRANT_AGE_SECONDS ||
    retentionExpiresAt <= now ||
    retentionExpiresAt < expiresAt ||
    retentionExpiresAt - now > MAX_RETENTION_SECONDS
  ) {
    return response.status(401).json({ error: 'invalid_meeting_retention_binding' });
  }

  request._agentEventBindingRetention = {
    isTemporary: true,
    expiredAt: new Date(retentionExpiresAt * 1000),
    suppressLangfuseTracing: true,
  };
  const endpoints = request.config?.endpoints ?? {};
  const agentConfig = endpoints.agents ?? {};
  request.config = {
    ...request.config,
    endpoints: {
      ...endpoints,
      agents: {
        ...agentConfig,
        checkpointer: {
          ...agentConfig.checkpointer,
          type: 'mongo',
          ttl: MAX_RETENTION_SECONDS,
          checkpointCollectionName: MEETING_CHECKPOINT_COLLECTION,
          checkpointWritesCollectionName: MEETING_CHECKPOINT_WRITES_COLLECTION,
        },
      },
    },
  };
  return next();
}

module.exports = applyViennaMeetingRetention;
module.exports.MAX_GRANT_AGE_SECONDS = MAX_GRANT_AGE_SECONDS;
