import { createToolRecoveryGuard, resolveToolRecoveryGuard, toolOutcome } from './recovery';
import { TOOL_FAILURE_MARKER, parseToolFailure } from '~/utils/failure';

const failure = (allowed: string, sideEffectState = 'none'): string =>
  `Tool failed.\n${TOOL_FAILURE_MARKER}${JSON.stringify({
    ok: false,
    error: { class: 'x', sideEffectState },
    recovery: { allowed, guidance: '…' },
  })}`;

const errored = (text: string) => ({ status: 'error' as const, text });
const guard = () => createToolRecoveryGuard({ maxRepairs: 2, maxFailuresPerRun: 4 });

describe('tool recovery guard', () => {
  it('lets every call through until something fails', () => {
    const run = guard();

    run.record('read_case', { id: '1' }, { status: 'success', text: 'ok' });

    expect(run.block('read_case', { id: '1' })).toBeUndefined();
    expect(run.block('send_letter', {})).toBeUndefined();
  });

  it('never re-sends a tool whose write may have landed, whatever the arguments', () => {
    const run = guard();

    run.record('send_letter', { to: 'a' }, errored(failure('verify', 'unknown')));

    expect(run.block('send_letter', { to: 'a' })).toContain('may have gone through');
    expect(run.block('send_letter', { to: 'b' })).toContain('read tool');
    expect(run.block('read_letter', { to: 'a' })).toBeUndefined();
  });

  it('treats an unknown side-effect state as verify even without recovery guidance', () => {
    const run = guard();
    const text = `${TOOL_FAILURE_MARKER}${JSON.stringify({ error: { sideEffectState: 'unknown' } })}`;

    run.record('send_letter', {}, errored(text));

    expect(run.block('send_letter', {})).toBeDefined();
  });

  it('allows the original call plus two repairs, then refuses', () => {
    const run = guard();

    for (const id of ['1', '2', '3']) {
      expect(run.block('update_case', { id })).toBeUndefined();
      run.record('update_case', { id }, errored(failure('repair')));
    }

    expect(run.block('update_case', { id: '4' })).toContain('too many times');
  });

  it('does not refuse a repair that succeeds', () => {
    const run = guard();

    run.record('update_case', { id: 'wrong' }, errored(failure('repair')));
    run.record('update_case', { id: 'right' }, { status: 'success', text: 'saved' });

    expect(run.block('update_case', { id: 'right' })).toBeUndefined();
  });

  it('gives a transient failure exactly one more attempt', () => {
    const run = guard();

    run.record('search', { q: 'x' }, errored(failure('retry_once')));
    expect(run.block('search', { q: 'x' })).toBeUndefined();
    run.record('search', { q: 'x' }, errored(failure('retry_once')));

    expect(run.block('search', { q: 'x' })).toContain('too many times');
  });

  it('blocks only the identical call after an ask-user or stop failure', () => {
    const run = guard();

    run.record('get_matter', { id: '1' }, errored(failure('ask_user')));

    expect(run.block('get_matter', { id: '1' })).toContain('repeating it cannot fix');
    expect(run.block('get_matter', { id: '2' })).toBeUndefined();
  });

  it('ignores argument key order when comparing calls', () => {
    const run = guard();

    run.record('get_matter', { a: 1, b: { y: 2, x: 1 } }, errored(failure('stop')));

    expect(run.block('get_matter', { b: { x: 1, y: 2 }, a: 1 })).toBeDefined();
  });

  it('allows one identical retry of a failure that carries no structured detail', () => {
    const run = guard();

    run.record('web_search', { q: 'x' }, errored('socket hang up'));
    expect(run.block('web_search', { q: 'x' })).toBeUndefined();
    run.record('web_search', { q: 'x' }, errored('socket hang up'));

    expect(run.block('web_search', { q: 'x' })).toContain('repeating it cannot fix');
    expect(run.block('web_search', { q: 'y' })).toBeUndefined();
  });

  it('counts a failure reported inside a successful result (HTTP Actions)', () => {
    const run = guard();

    run.record('post_note', {}, { status: 'success', text: failure('verify', 'unknown') });

    expect(run.block('post_note', {})).toBeDefined();
  });

  it('after the run-wide cap, refuses tools that already failed but not fresh ones', () => {
    const run = createToolRecoveryGuard({ maxRepairs: 5, maxFailuresPerRun: 2 });

    run.record('update_case', { id: '1' }, errored(failure('repair')));
    run.record('update_task', { id: '1' }, errored(failure('repair')));

    expect(run.block('update_case', { id: '2' })).toContain('too many times');
    expect(run.block('read_case', { id: '1' })).toBeUndefined();
  });
});

describe('resolveToolRecoveryGuard', () => {
  it('is off unless explicitly enabled', () => {
    expect(resolveToolRecoveryGuard(undefined)).toBeUndefined();
    expect(resolveToolRecoveryGuard({})).toBeUndefined();
    expect(resolveToolRecoveryGuard({ enabled: false, maxRepairs: 1 })).toBeUndefined();
  });

  it('applies the configured limits', () => {
    const run = resolveToolRecoveryGuard({ enabled: true, maxRepairs: 0 });

    run?.record('update_case', {}, errored(failure('repair')));

    expect(run?.block('update_case', { id: '2' })).toBeDefined();
  });
});

describe('toolOutcome', () => {
  it('joins the error message and string content, and ignores structured content', () => {
    expect(toolOutcome({ status: 'error', content: 'body', errorMessage: 'boom' }).text).toBe(
      'boom\nbody',
    );
    expect(toolOutcome({ status: 'success', content: [{ type: 'text' }] }).text).toBe('');
  });
});

describe('parseToolFailure', () => {
  it('reads the last envelope in the text and ignores malformed ones', () => {
    expect(parseToolFailure(`${failure('stop')}\n${failure('repair')}`)?.allowed).toBe('repair');
    expect(parseToolFailure(`x\n${TOOL_FAILURE_MARKER}{not json`)).toBeUndefined();
    expect(parseToolFailure(`x\n${TOOL_FAILURE_MARKER}null`)).toBeUndefined();
    expect(parseToolFailure('no envelope here')).toBeUndefined();
    expect(parseToolFailure(undefined)).toBeUndefined();
  });

  it('drops values it does not recognise', () => {
    const text = `${TOOL_FAILURE_MARKER}${JSON.stringify({
      error: { sideEffectState: 'maybe' },
      recovery: { allowed: 'explode' },
    })}`;

    expect(parseToolFailure(text)).toEqual({ allowed: undefined, sideEffectState: undefined });
  });
});
