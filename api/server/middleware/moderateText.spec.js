const axios = require('axios');
const { getBoundedAskUserAnswerValues, serializeAskUserAnswerVariants } = require('@librechat/api');
const { mockBedrockSend } = require('@aws-sdk/client-bedrock-runtime');
const originalAnthropicApiKey = process.env.ANTHROPIC_API_KEY;

jest.mock('axios', () => ({ post: jest.fn() }));
jest.mock('@aws-sdk/client-bedrock-runtime', () => ({
  BedrockRuntimeClient: jest
    .fn()
    .mockImplementation(() => ({ send: (...args) => mockBedrockSend(...args) })),
  ConverseCommand: jest.fn().mockImplementation((input) => ({ input })),
  mockBedrockSend: jest.fn(),
}));
jest.mock('winston-daily-rotate-file', () => jest.fn());
jest.mock('@librechat/api', () => ({
  isEnabled: () => true,
  getReferencedQuotes: () => undefined,
  mergeQuotedText: jest.fn(),
  getBoundedAskUserAnswerValues: jest.fn(() => []),
  serializeAskUserAnswerVariants: jest.fn(() => []),
}));
jest.mock('@librechat/data-schemas', () => ({ logger: { error: jest.fn(), warn: jest.fn() } }));
jest.mock('librechat-data-provider', () => ({ ErrorTypes: { MODERATION: 'moderation' } }));
jest.mock('./denyRequest', () => jest.fn());

const moderateText = require('./moderateText');

describe('moderateText', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    process.env.ANTHROPIC_API_KEY = 'test-key';
    mockBedrockSend.mockReset();
  });

  afterEach(() => {
    if (originalAnthropicApiKey === undefined) {
      delete process.env.ANTHROPIC_API_KEY;
    } else {
      process.env.ANTHROPIC_API_KEY = originalAnthropicApiKey;
    }
  });

  it('does not submit oversized answer batches to moderation', async () => {
    const answers = { environment: 'x'.repeat(16_001), credentials: 'safe' };
    const next = jest.fn();

    await moderateText({ body: { answers } }, {}, next);

    expect(getBoundedAskUserAnswerValues).toHaveBeenCalledWith(answers);
    expect(serializeAskUserAnswerVariants).toHaveBeenCalledWith(answers);
    expect(axios.post).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledTimes(1);
  });

  it('uses Claude Haiku after OpenAI moderation fails', async () => {
    axios.post
      .mockRejectedValueOnce(new Error('OpenAI unavailable'))
      .mockResolvedValueOnce({ data: { input_tokens: 12 } })
      .mockResolvedValueOnce({
        data: { content: [{ type: 'text', text: '{"results":[{"flagged":true}]}' }] },
      });
    const req = { body: { text: 'unsafe request' } };
    const res = {};
    const next = jest.fn();

    await moderateText(req, res, next);

    expect(axios.post).toHaveBeenNthCalledWith(
      2,
      'https://api.anthropic.com/v1/messages/count_tokens',
      expect.objectContaining({ model: 'claude-haiku-5-5' }),
      expect.objectContaining({ headers: expect.objectContaining({ 'x-api-key': 'test-key' }) }),
    );
    expect(axios.post).toHaveBeenNthCalledWith(
      3,
      'https://api.anthropic.com/v1/messages',
      expect.objectContaining({ model: 'claude-haiku-5-5', max_tokens: 256 }),
      expect.any(Object),
    );
    expect(mockBedrockSend).not.toHaveBeenCalled();
    expect(require('./denyRequest')).toHaveBeenCalledWith(req, res, { type: 'moderation' });
    expect(next).not.toHaveBeenCalled();
  });

  it('extracts the Anthropic key from a JSON Secrets Manager environment value', async () => {
    process.env.ANTHROPIC_API_KEY = JSON.stringify({ api_key: 'json-test-key' });
    axios.post
      .mockRejectedValueOnce(new Error('OpenAI unavailable'))
      .mockResolvedValueOnce({ data: { input_tokens: 12 } })
      .mockResolvedValueOnce({
        data: { content: [{ type: 'text', text: '{"results":[{"flagged":false}]}' }] },
      });
    const next = jest.fn();

    await moderateText({ body: { text: 'safe request' } }, {}, next);

    expect(axios.post).toHaveBeenNthCalledWith(
      2,
      'https://api.anthropic.com/v1/messages/count_tokens',
      expect.any(Object),
      expect.objectContaining({
        headers: expect.objectContaining({ 'x-api-key': 'json-test-key' }),
      }),
    );
    expect(next).toHaveBeenCalledTimes(1);
  });

  it('uses Bedrock after Claude token counting fails and preserves fail closed behavior', async () => {
    axios.post
      .mockRejectedValueOnce(new Error('OpenAI unavailable'))
      .mockRejectedValueOnce(new Error('Claude unavailable'));
    mockBedrockSend.mockResolvedValueOnce({
      output: { message: { content: [{ text: '{"results":[{"flagged":true}]}' }] } },
    });
    const req = { body: { text: 'unsafe request' } };
    const res = {};
    const next = jest.fn();

    await moderateText(req, res, next);

    expect(mockBedrockSend).toHaveBeenCalledTimes(1);
    expect(require('./denyRequest')).toHaveBeenCalledWith(req, res, { type: 'moderation' });
    expect(next).not.toHaveBeenCalled();
  });

  it('skips paid Claude moderation when token counting exceeds the budget', async () => {
    axios.post
      .mockRejectedValueOnce(new Error('OpenAI unavailable'))
      .mockResolvedValueOnce({ data: { input_tokens: 100_001 } });
    mockBedrockSend.mockResolvedValueOnce({
      output: { message: { content: [{ text: '{"results":[{"flagged":false}]}' }] } },
    });
    const next = jest.fn();

    await moderateText({ body: { text: 'large request' } }, {}, next);

    expect(axios.post).toHaveBeenCalledTimes(2);
    expect(axios.post.mock.calls[1][0]).toBe('https://api.anthropic.com/v1/messages/count_tokens');
    expect(mockBedrockSend).toHaveBeenCalledTimes(1);
    expect(next).toHaveBeenCalledTimes(1);
  });
});
