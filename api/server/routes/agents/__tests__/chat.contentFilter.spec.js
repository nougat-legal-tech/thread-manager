const mockGetFiles = jest.fn();
const mockPiiMiddleware = jest.fn((_req, _res, next) => next());
const mockModerateText = jest.fn((_req, _res, next) => next());
const mockCreateMessageFilterPii = jest.fn(() => mockPiiMiddleware);
const mockGetSafeErrorMetadata = jest.fn((error) => ({
  type: error instanceof Error ? 'Error' : 'UnknownError',
  ...(Number.isInteger(error?.response?.status) && { status: error.response.status }),
}));
const mockLogger = {
  warn: jest.fn(),
};

jest.mock('@librechat/data-schemas', () => ({
  logger: mockLogger,
}));

jest.mock('@librechat/api', () => ({
  createMessageFilterPii: mockCreateMessageFilterPii,
  generateCheckAccess: jest.fn(() => (_req, _res, next) => next()),
  skipAgentCheck: jest.fn(),
  applyResumeContext: jest.fn(),
  GenerationJobManager: {
    getJob: jest.fn(),
  },
  getSafeErrorMetadata: mockGetSafeErrorMetadata,
}));

jest.mock('~/server/middleware', () => ({
  moderateText: mockModerateText,
  validateConvoAccess: (_req, _res, next) => next(),
  buildEndpointOption: (_req, _res, next) => next(),
  canAccessAgentFromBody: jest.fn(() => (_req, _res, next) => next()),
}));

jest.mock('~/server/middleware/validate/subagentThreadTurn', () => (_req, _res, next) => next());

jest.mock('~/server/services/Endpoints/agents', () => ({
  initializeClient: jest.fn(),
}));
jest.mock('~/server/controllers/agents/request', () => jest.fn());
jest.mock('~/server/controllers/agents/resume', () => jest.fn());
jest.mock('~/server/services/Endpoints/agents/title', () => jest.fn());
jest.mock('~/models', () => ({
  getFiles: mockGetFiles,
  getRoleByName: jest.fn(),
}));

describe('agents chat content filtering', () => {
  it('provides owner-scoped canonical file lookup to the message filter', () => {
    require('../chat');

    expect(mockCreateMessageFilterPii).toHaveBeenCalledTimes(1);
    expect(mockCreateMessageFilterPii.mock.calls[0][0]).toMatchObject({
      getFiles: mockGetFiles,
    });
  });

  it('registers meeting retention before transcript content filters', () => {
    const router = require('../chat');
    const middleware = router.stack.map((layer) => layer.handle);
    const retentionIndex = middleware.findIndex(
      (handler) => handler.name === 'applyViennaMeetingRetention',
    );

    expect(retentionIndex).toBeGreaterThanOrEqual(0);
    expect(retentionIndex).toBeLessThan(middleware.indexOf(mockPiiMiddleware));
    expect(retentionIndex).toBeLessThan(middleware.indexOf(mockModerateText));
  });

  it('logs only bounded metadata when resume context restoration fails', async () => {
    const api = require('@librechat/api');
    const router = require('../chat');
    const restoreResumeContext = router.stack.find(
      (layer) => layer.handle?.name === 'restoreResumeContext',
    )?.handle;
    const rawValue = 'PRIVATE-RESUME-CONTEXT';
    const restoreError = Object.assign(new Error(rawValue), {
      response: { status: 503, data: rawValue },
    });
    api.GenerationJobManager.getJob.mockRejectedValueOnce(restoreError);
    const next = jest.fn();

    await restoreResumeContext(
      {
        path: '/resume',
        body: { conversationId: 'conversation-1' },
      },
      {},
      next,
    );

    expect(mockGetSafeErrorMetadata).toHaveBeenCalledWith(restoreError);
    expect(mockLogger.warn).toHaveBeenCalledWith('[agents/chat] Failed to restore resume context', {
      type: 'Error',
      status: 503,
    });
    expect(JSON.stringify(mockLogger.warn.mock.calls)).not.toContain(rawValue);
    expect(next).toHaveBeenCalledTimes(1);
  });
});
