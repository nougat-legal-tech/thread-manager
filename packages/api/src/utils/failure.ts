/**
 * Structured tool failure, shared with the Django MCP sidecar and chat_proxy so
 * every surface describes a failed tool call the same way. The envelope tells the
 * model what kind of failure it was, whether the operation can have taken effect,
 * and what it may do next.
 */
export const TOOL_FAILURE_MARKER = 'tool_failure=';

export type ToolFailureClass =
  | 'transient'
  | 'invalid_input'
  | 'not_found'
  | 'conflict'
  | 'unknown_outcome'
  | 'reauth_required'
  | 'permission_denied'
  | 'internal';

export type ToolSideEffectState = 'none' | 'unknown';

export type ToolRecoveryStep = 'retry_once' | 'repair' | 'verify' | 'ask_user' | 'stop';

export interface ToolFailureEnvelope {
  ok: false;
  error: {
    class: ToolFailureClass;
    httpStatus: number | null;
    retryable: boolean;
    sideEffectState: ToolSideEffectState;
    method: string;
  };
  recovery: { allowed: ToolRecoveryStep; guidance: string };
}

export interface ToolFailureInput {
  /** HTTP method of the failed request; anything but GET is treated as a write. */
  method?: string;
  /** Response status, when the server answered. */
  status?: number;
  /** True when the request provably never reached the server (refused, DNS failure). */
  neverSent?: boolean;
}

const TRANSIENT_STATUSES = new Set([408, 425, 429, 502, 503, 504]);

const STATUS_CLASSES = new Map<number, ToolFailureClass>([
  [400, 'invalid_input'],
  [401, 'reauth_required'],
  [403, 'permission_denied'],
  [404, 'not_found'],
  [409, 'conflict'],
  [422, 'invalid_input'],
]);

const RECOVERY: Record<ToolFailureClass, ToolFailureEnvelope['recovery']> = {
  transient: {
    allowed: 'retry_once',
    guidance: 'Retry at most once more, then report the failure.',
  },
  invalid_input: {
    allowed: 'repair',
    guidance:
      'Fix the rejected input (look up the right id or field with a read tool) and call again, at most twice.',
  },
  not_found: {
    allowed: 'repair',
    guidance: 'Look up the correct identifier with a read tool and call again, at most twice.',
  },
  conflict: {
    allowed: 'repair',
    guidance: 'Re-read the current state, then call again only if it is still needed.',
  },
  unknown_outcome: {
    allowed: 'verify',
    guidance:
      'The request may have gone through. Do NOT call this tool again. Check the result with a read tool, then tell the user what you found.',
  },
  reauth_required: {
    allowed: 'ask_user',
    guidance: 'Tell the user to sign in or reconnect the integration. Do not retry.',
  },
  permission_denied: {
    allowed: 'ask_user',
    guidance: 'Tell the user they lack access and who can grant it. Do not retry.',
  },
  internal: {
    allowed: 'stop',
    guidance: 'Do not retry. Report the failure and that nothing was changed.',
  },
};

function classifyStatus(status: number): ToolFailureClass {
  if (TRANSIENT_STATUSES.has(status)) {
    return 'transient';
  }
  const known = STATUS_CLASSES.get(status);
  if (known) {
    return known;
  }
  return status >= 500 ? 'internal' : 'invalid_input';
}

/**
 * Classifies one failed HTTP tool call. A read, a 4xx refusal and a request that
 * never left are provably side-effect free; a write that timed out or met a 5xx
 * may have taken effect, so it is always `unknown_outcome` and never retryable.
 */
export function buildToolFailureEnvelope({
  method,
  status,
  neverSent = false,
}: ToolFailureInput): ToolFailureEnvelope {
  const verb = (method ?? 'GET').toUpperCase();
  const isRead = verb === 'GET';
  const answered = typeof status === 'number';
  const refused = neverSent || (answered && status < 500);
  const sideEffectState: ToolSideEffectState = isRead || refused ? 'none' : 'unknown';
  const baseClass = answered ? classifyStatus(status) : 'transient';
  const failureClass = sideEffectState === 'unknown' ? 'unknown_outcome' : baseClass;

  return {
    ok: false,
    error: {
      class: failureClass,
      httpStatus: answered ? status : null,
      retryable: failureClass === 'transient',
      sideEffectState,
      method: verb,
    },
    recovery: RECOVERY[failureClass],
  };
}

export function formatToolFailure(envelope: ToolFailureEnvelope): string {
  return `${TOOL_FAILURE_MARKER}${JSON.stringify(envelope)}`;
}
