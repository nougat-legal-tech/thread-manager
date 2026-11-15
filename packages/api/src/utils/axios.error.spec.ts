import { AxiosError, AxiosHeaders } from 'axios';
import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios';
import type { ToolFailureEnvelope } from './failure';
import { buildToolFailureEnvelope, TOOL_FAILURE_MARKER } from './failure';
import { describeToolRequestError } from './axios';

jest.mock('@librechat/data-schemas', () => ({
  logger: { error: jest.fn(), warn: jest.fn(), debug: jest.fn(), info: jest.fn() },
}));

const requestConfig = (method: string): InternalAxiosRequestConfig =>
  ({ headers: new AxiosHeaders(), method }) as InternalAxiosRequestConfig;

const responseError = (
  status: number,
  data: AxiosResponse['data'],
  method = 'post',
): AxiosError => {
  const config = requestConfig(method);
  const response: AxiosResponse = { status, statusText: '', headers: {}, config, data };
  return new AxiosError(`Request failed with status code ${status}`, 'ERR', config, {}, response);
};

const transportError = (code: string, method: string): AxiosError =>
  new AxiosError('no response', code, requestConfig(method), {});

const envelopeOf = (text: string): ToolFailureEnvelope =>
  JSON.parse(text.slice(text.lastIndexOf(TOOL_FAILURE_MARKER) + TOOL_FAILURE_MARKER.length));

describe('describeToolRequestError', () => {
  const message = 'API call to api.example.test failed:';

  it('keeps the response body so the model can see what was rejected', () => {
    const error = responseError(400, { detail: 'caseId is required.', code: 'invalid_input' });

    const text = describeToolRequestError({ message, error });

    expect(text).toContain('responded with status 400');
    expect(text).toContain(
      'Response body: {"detail":"caseId is required.","code":"invalid_input"}',
    );
  });

  it('truncates very large bodies', () => {
    const text = describeToolRequestError({ message, error: responseError(500, 'x'.repeat(5000)) });
    const body = text.slice(0, text.lastIndexOf(`\n${TOOL_FAILURE_MARKER}`));

    expect(body.length).toBeLessThan(2300);
    expect(body.endsWith('…')).toBe(true);
  });

  it('adds no body when there is no response or no body', () => {
    expect(describeToolRequestError({ message, error: responseError(502, '') })).not.toContain(
      'Response body',
    );
    expect(describeToolRequestError({ message, error: new Error('socket hang up') })).not.toContain(
      'Response body',
    );
  });

  it('ends an HTTP failure with a structured failure the model can act on', () => {
    const envelope = envelopeOf(
      describeToolRequestError({ message, error: responseError(404, { detail: 'No such case.' }) }),
    );

    expect(envelope.error.class).toBe('not_found');
    expect(envelope.error.sideEffectState).toBe('none');
    expect(envelope.recovery.allowed).toBe('repair');
  });

  it('never invites a re-send of a write that may have landed', () => {
    const gateway = envelopeOf(
      describeToolRequestError({ message, error: responseError(502, { detail: 'Bad gateway' }) }),
    );
    const timeout = envelopeOf(
      describeToolRequestError({ message, error: transportError('ECONNABORTED', 'post') }),
    );

    for (const envelope of [gateway, timeout]) {
      expect(envelope.error.class).toBe('unknown_outcome');
      expect(envelope.error.retryable).toBe(false);
      expect(envelope.recovery.allowed).toBe('verify');
      expect(envelope.recovery.guidance).toContain('Do NOT call this tool again');
    }
  });

  it('treats a request that never left as safe to retry, even for a write', () => {
    const envelope = envelopeOf(
      describeToolRequestError({ message, error: transportError('ECONNREFUSED', 'post') }),
    );

    expect(envelope.error.class).toBe('transient');
    expect(envelope.error.sideEffectState).toBe('none');
    expect(envelope.recovery.allowed).toBe('retry_once');
  });

  it('leaves non-HTTP errors without a structured failure', () => {
    expect(describeToolRequestError({ message, error: new Error('boom') })).not.toContain(
      TOOL_FAILURE_MARKER,
    );
  });
});

describe('buildToolFailureEnvelope', () => {
  it.each([
    [400, 'invalid_input', 'repair'],
    [401, 'reauth_required', 'ask_user'],
    [403, 'permission_denied', 'ask_user'],
    [404, 'not_found', 'repair'],
    [409, 'conflict', 'repair'],
    [422, 'invalid_input', 'repair'],
    [429, 'transient', 'retry_once'],
  ])('classifies a refused write with status %i as %s', (status, failureClass, allowed) => {
    const envelope = buildToolFailureEnvelope({ method: 'POST', status });

    expect(envelope.error.class).toBe(failureClass);
    expect(envelope.error.sideEffectState).toBe('none');
    expect(envelope.recovery.allowed).toBe(allowed);
  });

  it('never marks a failed read as an unknown outcome', () => {
    expect(buildToolFailureEnvelope({ method: 'get', status: 500 }).error.class).toBe('internal');
    expect(buildToolFailureEnvelope({ method: 'get', status: 503 }).error.class).toBe('transient');
    expect(buildToolFailureEnvelope({ method: 'get' }).error.class).toBe('transient');
    expect(buildToolFailureEnvelope({}).error.sideEffectState).toBe('none');
  });

  it('marks every unanswered or 5xx write as an unknown outcome', () => {
    for (const status of [500, 502, 503, 504, undefined]) {
      const envelope = buildToolFailureEnvelope({ method: 'PATCH', status });

      expect(envelope.error.class).toBe('unknown_outcome');
      expect(envelope.error.retryable).toBe(false);
    }
  });
});
