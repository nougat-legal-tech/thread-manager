import { expect, test } from '@playwright/test';
import {
  MOCK_ENDPOINTS,
  NEW_CHAT_PATH,
  messagesView,
  selectMockEndpoint,
  sendMessageAndWaitForCompletion,
} from './helpers';

test.describe('generation finalization invariant', () => {
  test('rejects a persisted assistant error after successful generation admission', async ({
    page,
  }) => {
    test.setTimeout(90_000);
    const label = `completion-error-${Date.now()}`;
    const providerError = `E2E forced stream error ${label}`;
    const providerHeadline = `${MOCK_ENDPOINTS[0].label} could not complete this request.`;

    await page.goto(NEW_CHAT_PATH, { timeout: 10000 });
    await selectMockEndpoint(page, MOCK_ENDPOINTS[0]);

    await expect(
      sendMessageAndWaitForCompletion(page, `E2E_FORCED_ERROR:${label}`),
    ).rejects.toThrow('Persisted assistant response contains an unexpected error');
    await expect(messagesView(page).getByText(providerHeadline)).toBeVisible();
    /** Long provider responses are intentionally collapsed under the Provider response control. */
    await messagesView(page).getByRole('button', { name: 'Provider response' }).click();
    await expect(messagesView(page).getByText(providerError)).toBeVisible();
  });
});
