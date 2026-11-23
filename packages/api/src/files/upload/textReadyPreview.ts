const MAX_TEXT_READY_PREVIEW_CHARS = 100_000;

const SPREADSHEET_EXTENSIONS = /\.(csv|xlsx)$/i;

export function isTextReadySpreadsheetUpload({ filename }: { filename?: string | null }): boolean {
  return SPREADSHEET_EXTENSIONS.test(filename ?? '');
}

export function buildTextReadyPreview(text: string): { text: string; truncated: boolean } {
  if (typeof text !== 'string' || text.trim().length === 0) {
    throw new Error('No readable spreadsheet text was produced');
  }

  return {
    text: text.slice(0, MAX_TEXT_READY_PREVIEW_CHARS),
    truncated: text.length > MAX_TEXT_READY_PREVIEW_CHARS,
  };
}
