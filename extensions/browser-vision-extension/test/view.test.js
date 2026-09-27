const view = require('../tools/view');

describe('view tool', () => {
  let capture;

  beforeEach(() => {
    capture = {
      hasPage: true,
      navigate: jest.fn(),
      screenshot: jest.fn().mockResolvedValue('/tmp/bv-capture-123.png'),
      pageUrl: 'https://example.com',
      logAction: jest.fn(),
      waitForMs: jest.fn(),
    };
  });

  it('returns grid metadata with default precision 48', async () => {
    const result = await view.handler({ url: 'https://example.com' }, () => capture);

    expect(result.error).toBeUndefined();
    expect(result.grid.precision).toBe(48);
    expect(result.grid.cols).toBe(20);
    expect(result.grid.rows).toBe(20);
    expect(result.grid.cell_width_px).toBe(48);
    expect(result.grid.cell_height_px).toBe(48);
    expect(result.grid.viewport).toEqual({ width: 960, height: 960 });
  });

  it('returns grid metadata for precision 96', async () => {
    const result = await view.handler(
      { url: 'https://example.com', precision: 96 },
      () => capture
    );

    expect(result.error).toBeUndefined();
    expect(result.grid.precision).toBe(96);
    expect(result.grid.cols).toBe(10);
    expect(result.grid.rows).toBe(10);
    expect(result.grid.cell_width_px).toBe(96);
    expect(result.grid.cell_height_px).toBe(96);
  });

  it('returns INVALID_PRECISION for precision 0', async () => {
    const result = await view.handler(
      { url: 'https://example.com', precision: 0 },
      () => capture
    );

    expect(result.error).toBe('INVALID_PRECISION');
    expect(capture.navigate).not.toHaveBeenCalled();
  });

  it('returns INVALID_PRECISION for non-integer precision 48.5', async () => {
    const result = await view.handler(
      { url: 'https://example.com', precision: 48.5 },
      () => capture
    );

    expect(result.error).toBe('INVALID_PRECISION');
    expect(capture.navigate).not.toHaveBeenCalled();
  });

  it('returns INVALID_PRECISION for negative precision -1', async () => {
    const result = await view.handler(
      { url: 'https://example.com', precision: -1 },
      () => capture
    );

    expect(result.error).toBe('INVALID_PRECISION');
    expect(capture.navigate).not.toHaveBeenCalled();
  });

  it('returns INVALID_PRECISION for precision 961', async () => {
    const result = await view.handler(
      { url: 'https://example.com', precision: 961 },
      () => capture
    );

    expect(result.error).toBe('INVALID_PRECISION');
    expect(capture.navigate).not.toHaveBeenCalled();
  });

  it('returns structured result with screenshot and metadata fields', async () => {
    const result = await view.handler({ url: 'https://example.com' }, () => capture);

    expect(result.screenshot_path).toBe('/tmp/bv-capture-123.png');
    expect(result.screenshot_width).toBe(960);
    expect(result.screenshot_height).toBe(960);
    expect(result.grid).toBeDefined();
    expect(result.url).toBe('https://example.com');
    expect(result.timestamp).toBeDefined();
    expect(typeof result.timestamp).toBe('string');
  });
});
