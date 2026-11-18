import { parseToolFailure } from '~/utils/failure';

/**
 * Tool recovery budget for one agent run.
 *
 * A failing tool says what the caller may do next (`recovery.allowed` in its
 * structured failure). The model usually follows that, but nothing made it: a
 * model that keeps re-calling a failed tool can loop, or re-send a write whose
 * outcome is unknown. This guard is where the rule is enforced, per run:
 *
 * - `verify`  (a write may have landed): the tool is not sent again this run.
 * - `repair`: the original call plus `maxRepairs` more.
 * - `retry_once`: one more attempt.
 * - `ask_user` / `stop`, or a failure with no structured detail: the identical
 *   call is not repeated (a plain failure gets one identical retry first).
 * - After `maxFailuresPerRun` failures, tools that already failed are refused;
 *   tools that have not failed still run, so the model can read and report.
 */
export interface ToolRecoveryLimits {
  maxRepairs: number;
  maxFailuresPerRun: number;
}

export interface ToolOutcome {
  status: 'success' | 'error';
  /** Everything the tool returned as text: its content and/or its error message. */
  text?: string;
}

export interface ToolRecoveryGuard {
  /** Returns the refusal to hand the model, or `undefined` when the call may run. */
  block: (name: string, args: unknown) => string | undefined;
  record: (name: string, args: unknown, outcome: ToolOutcome) => void;
}

type BlockReason = 'unknown_outcome' | 'repeat' | 'budget';

const REFUSALS: Record<BlockReason, string> = {
  unknown_outcome:
    'An earlier call to this tool may have gone through, so it was not sent again. ' +
    'Verify the result with a read tool, then tell the user what you found.',
  repeat:
    'This exact call already failed in a way that repeating it cannot fix, so it was not sent again. ' +
    'Tell the user what failed and what they can do.',
  budget:
    'This tool has failed too many times this turn, so it was not sent again. ' +
    'Tell the user what failed, what you tried, and that nothing further was changed.',
};

/** One identical retry is allowed for a failure that carries no structured detail. */
const PLAIN_FAILURE_REPEATS = 1;

function callKey(name: string, args: unknown): string {
  let serialized: string;
  try {
    serialized = JSON.stringify(args, (_key, value: unknown) =>
      value != null && typeof value === 'object' && !Array.isArray(value)
        ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)))
        : value,
    );
  } catch {
    serialized = String(args);
  }
  return `${name}:${serialized}`;
}

export function createToolRecoveryGuard(limits: ToolRecoveryLimits): ToolRecoveryGuard {
  const verifyOnly = new Set<string>();
  const noRepeat = new Set<string>();
  const attempts = new Map<string, number>();
  const plainFailures = new Map<string, number>();
  let failures = 0;

  const reasonFor = (name: string, args: unknown): BlockReason | undefined => {
    if (verifyOnly.has(name)) {
      return 'unknown_outcome';
    }
    if (noRepeat.has(callKey(name, args))) {
      return 'repeat';
    }
    const spent = attempts.get(name);
    if (spent != null && (spent > limits.maxRepairs || failures >= limits.maxFailuresPerRun)) {
      return 'budget';
    }
    return undefined;
  };

  return {
    block: (name, args) => {
      const reason = reasonFor(name, args);
      return reason == null ? undefined : REFUSALS[reason];
    },
    record: (name, args, outcome) => {
      const failure = parseToolFailure(outcome.text);
      if (outcome.status !== 'error' && failure == null) {
        return;
      }
      failures += 1;
      if (failure?.allowed === 'verify' || failure?.sideEffectState === 'unknown') {
        verifyOnly.add(name);
        return;
      }
      if (failure?.allowed === 'repair') {
        attempts.set(name, (attempts.get(name) ?? 0) + 1);
        return;
      }
      if (failure?.allowed === 'retry_once') {
        /** The first failure spends one attempt; a second spends the whole budget. */
        const spent = attempts.get(name);
        attempts.set(name, spent == null ? 1 : spent + limits.maxRepairs + 1);
        return;
      }
      const key = callKey(name, args);
      if (failure?.allowed != null) {
        noRepeat.add(key);
        return;
      }
      const seen = (plainFailures.get(key) ?? 0) + 1;
      plainFailures.set(key, seen);
      if (seen > PLAIN_FAILURE_REPEATS) {
        noRepeat.add(key);
      }
    },
  };
}

export interface ToolRecoveryConfig {
  enabled?: boolean;
  maxRepairs?: number;
  maxFailuresPerRun?: number;
}

/** Builds the run's guard from `endpoints.agents.toolRecovery`, or none when it is off. */
export function resolveToolRecoveryGuard(
  config: ToolRecoveryConfig | undefined,
): ToolRecoveryGuard | undefined {
  if (config?.enabled !== true) {
    return undefined;
  }
  return createToolRecoveryGuard({
    maxRepairs: config.maxRepairs ?? 2,
    maxFailuresPerRun: config.maxFailuresPerRun ?? 4,
  });
}

/** The text of a settled tool result, wherever the tool put it. */
export function toolOutcome(result: {
  status: 'success' | 'error';
  content: string | unknown[];
  errorMessage?: string;
}): ToolOutcome {
  const parts = [result.errorMessage, typeof result.content === 'string' ? result.content : ''];
  return { status: result.status, text: parts.filter(Boolean).join('\n') };
}
