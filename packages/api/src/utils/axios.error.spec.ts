import { AxiosError, AxiosHeaders } from 'axios';
import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios';
import { describeToolRequestError } from './axios';

jest.mock('@librechat/data-schemas', () => ({
  logger: { error: jest.fn(), warn: jest.fn(), debug: jest.fn(), info: jest.fn() },
}));

const responseError = (status: number, data: AxiosResponse['data']): AxiosError => {
  const config = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
  const response: AxiosResponse = { status, statusText: '', headers: {}, config, data };
  return new AxiosError(`Request failed with status code ${status}`, 'ERR', config, {}, response);
};

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

    expect(text.length).toBeLessThan(2300);
    expect(text.endsWith('…')).toBe(true);
  });

  it('adds nothing when there is no response or no body', () => {
    expect(describeToolRequestError({ message, error: responseError(502, '') })).not.toContain(
      'Response body',
    );
    expect(describeToolRequestError({ message, error: new Error('socket hang up') })).not.toContain(
      'Response body',
    );
  });
});
