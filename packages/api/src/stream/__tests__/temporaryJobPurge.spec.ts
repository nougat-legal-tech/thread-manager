/** @jest-environment node */

jest.spyOn(console, 'log').mockImplementation();

async function configureManager() {
  const { GenerationJobManager } = await import('../GenerationJobManager');
  const { InMemoryJobStore } = await import('../implementations/InMemoryJobStore');
  const { InMemoryEventTransport } = await import('../implementations/InMemoryEventTransport');
  const jobStore = new InMemoryJobStore();
  const eventTransport = new InMemoryEventTransport();
  GenerationJobManager.configure({
    jobStore,
    eventTransport,
    isRedis: false,
    cleanupOnComplete: false,
  });
  GenerationJobManager.initialize();
  return { manager: GenerationJobManager, jobStore, eventTransport };
}

describe('temporary generation purge', () => {
  beforeEach(() => {
    jest.resetModules();
  });

  it('deletes a completed temporary job and cleans its event transport', async () => {
    const { manager, jobStore, eventTransport } = await configureManager();
    const streamId = 'temporary-meeting-session';
    const job = await manager.createJob(streamId, 'owner-1', streamId, {
      initialMetadata: {
        isTemporary: true,
        retentionExpiresAt: new Date(Date.now() + 60_000).toISOString(),
      },
    });
    await manager.abortJob(streamId, {
      expectedCreatedAt: job.createdAt,
      awaitProviderDrain: true,
    });
    const cleanup = jest.spyOn(eventTransport, 'cleanup');

    await expect(manager.purgeTemporaryJob(streamId, job.createdAt, 'owner-1')).resolves.toBe(true);
    await expect(jobStore.getJob(streamId)).resolves.toBeNull();
    expect(cleanup).toHaveBeenCalledWith(streamId);
    await manager.destroy();
  });

  it('does not delete a running temporary job or another owner’s job', async () => {
    const { manager, jobStore } = await configureManager();
    const streamId = 'temporary-meeting-running';
    const job = await manager.createJob(streamId, 'owner-1', streamId, {
      initialMetadata: { isTemporary: true },
    });

    await expect(manager.purgeTemporaryJob(streamId, job.createdAt, 'owner-1')).resolves.toBe(
      false,
    );
    await expect(manager.purgeTemporaryJob(streamId, job.createdAt, 'owner-2')).resolves.toBe(
      false,
    );
    await expect(jobStore.getJob(streamId)).resolves.toMatchObject({ status: 'running' });
    await manager.destroy();
  });

  it('does not change completed persistent-job behavior', async () => {
    const { manager, jobStore } = await configureManager();
    const streamId = 'persistent-conversation';
    const job = await manager.createJob(streamId, 'owner-1', streamId);
    await manager.abortJob(streamId, { expectedCreatedAt: job.createdAt });

    await expect(manager.purgeTemporaryJob(streamId, job.createdAt, 'owner-1')).resolves.toBe(
      false,
    );
    await expect(jobStore.getJob(streamId)).resolves.not.toBeNull();
    await manager.destroy();
  });

  it('fails closed instead of acknowledging when the stream now contains a replacement generation', async () => {
    const { manager, jobStore } = await configureManager();
    const streamId = 'temporary-meeting-replaced';
    const job = await manager.createJob(streamId, 'owner-1', streamId, {
      initialMetadata: { isTemporary: true },
    });
    const current = await jobStore.getJob(streamId);
    if (current == null) throw new Error('expected created job');
    const replacement = {
      ...current,
      createdAt: current.createdAt + 1,
      status: 'running' as const,
    };
    jest.spyOn(jobStore, 'getJob').mockResolvedValue(replacement);
    const deleteJob = jest.spyOn(jobStore, 'deleteJob');

    await expect(manager.purgeTemporaryJob(streamId, job.createdAt, 'owner-1')).resolves.toBe(
      false,
    );
    expect(deleteJob).not.toHaveBeenCalled();
    await manager.destroy();
  });
});
