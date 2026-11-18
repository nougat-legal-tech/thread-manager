jest.mock('./prewarm', () => ({
  markSandboxReady: jest.fn(),
}));

import type {
  ToolExecuteBatchRequest,
  ToolExecuteResult,
  ToolCallRequest,
} from '@librechat/agents';
import type { ToolExecuteOptions } from './handlers';
import type { ServerRequest } from '~/types';
import { createToolExecuteHandler } from './handlers';
import { TOOL_FAILURE_MARKER } from '~/utils/failure';

const unknownOutcome = `502 Bad Gateway\n${TOOL_FAILURE_MARKER}${JSON.stringify({
  ok: false,
  error: { class: 'unknown_outcome', sideEffectState: 'unknown' },
  recovery: { allowed: 'verify', guidance: 'Do NOT call this tool again.' },
})}`;

const requestWith = (toolRecovery?: { enabled: boolean }): ServerRequest =>
  ({ config: { endpoints: { agents: { toolRecovery } } } }) as unknown as ServerRequest;

function setup(toolRecovery?: { enabled: boolean }) {
  const send = jest.fn(async () => {
    throw new Error(unknownOutcome);
  });
  const read = jest.fn(async () => ({ content: 'letter not found' }));
  const loadTools: ToolExecuteOptions['loadTools'] = jest.fn(async () => ({
    loadedTools: [
      { name: 'send_letter', invoke: send },
      { name: 'read_letter', invoke: read },
    ] as never[],
  }));
  const handler = createToolExecuteHandler({ loadTools });
  const run = (toolCalls: ToolCallRequest[]): Promise<ToolExecuteResult[]> =>
    new Promise((resolve, reject) => {
      const request: ToolExecuteBatchRequest = {
        toolCalls,
        resolve,
        reject,
        configurable: { req: requestWith(toolRecovery) },
      };
      void handler.handle('on_tool_execute', request);
    });
  return { run, send, read };
}

const call = (id: string, name: string, args: Record<string, unknown> = {}): ToolCallRequest => ({
  id,
  name,
  args,
});

describe('tool recovery budget in the tool-execute handler', () => {
  it('does not re-send a write whose outcome is unknown, in a later batch of the same run', async () => {
    const test = setup({ enabled: true });

    const [first] = await test.run([call('1', 'send_letter', { to: 'a' })]);
    const [second, verify] = await test.run([
      call('2', 'send_letter', { to: 'b' }),
      call('3', 'read_letter'),
    ]);

    expect(first.status).toBe('error');
    expect(first.errorMessage).toContain(TOOL_FAILURE_MARKER);
    expect(second.status).toBe('error');
    expect(second.errorMessage).toContain('may have gone through');
    expect(test.send).toHaveBeenCalledTimes(1);
    expect(verify.status).toBe('success');
    expect(test.read).toHaveBeenCalledTimes(1);
  });

  it('does not count its own refusals as further failures', async () => {
    const test = setup({ enabled: true });

    await test.run([call('1', 'send_letter')]);
    for (const id of ['2', '3', '4', '5', '6']) {
      await test.run([call(id, 'send_letter')]);
    }
    const [read] = await test.run([call('7', 'read_letter')]);

    expect(test.send).toHaveBeenCalledTimes(1);
    expect(read.status).toBe('success');
  });

  it('keeps one budget per handler, so a new run starts clean', async () => {
    const firstRun = setup({ enabled: true });
    await firstRun.run([call('1', 'send_letter')]);

    const nextRun = setup({ enabled: true });
    await nextRun.run([call('1', 'send_letter')]);

    expect(nextRun.send).toHaveBeenCalledTimes(1);
  });

  it.each([undefined, { enabled: false }])(
    'changes nothing when the budget is not enabled (%p)',
    async (toolRecovery) => {
      const test = setup(toolRecovery);

      await test.run([call('1', 'send_letter')]);
      const [second] = await test.run([call('2', 'send_letter')]);

      expect(test.send).toHaveBeenCalledTimes(2);
      expect(second.errorMessage).toContain(TOOL_FAILURE_MARKER);
    },
  );
});
