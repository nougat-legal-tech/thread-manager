import { buildTextReadyPreview, isTextReadySpreadsheetUpload } from './textReadyPreview';

describe('text-ready spreadsheet previews', () => {
  it.each(['pipeline.csv', 'Pipeline.XLSX'])(
    'recognizes %s as a spreadsheet attachment',
    (filename) => {
      expect(isTextReadySpreadsheetUpload({ filename })).toBe(true);
    },
  );

  it('bounds preview text and marks it when rows were truncated', () => {
    const text = 'row,lead@example.com\n'.repeat(5_001);
    const preview = buildTextReadyPreview(text);

    expect(preview.text).toHaveLength(100_000);
    expect(preview.truncated).toBe(true);
  });

  it('rejects empty extraction so an unreadable workbook is not presented as ready', () => {
    expect(() => buildTextReadyPreview('  \n')).toThrow('No readable spreadsheet text');
  });
});
