const click = require('../tools/click');

function makeCapture({ hasPage = true } = {}) {
  return {
    hasPage,
    screenshot: jest.fn((prefix) => Promise.resolve(`/tmp/bv-${prefix}-123.png`)),
    click: jest.fn(),
    logAction: jest.fn(),
    waitForMs: jest.fn(),
  };
}

describe('click tool', () => {
  test('valid click col0 row0 precision48 returns clicked_at {x:24,y:24} with pre/post screenshot paths and grid', async () => {
    const capture = makeCapture();

    const result = await click.handler(
      { col: 0, row: 0, precision: 48 },
      () => capture
    );

    expect(result.clicked_at).toEqual({ x: 24, y: 24 });
    expect(result.pre_click_screenshot_path).toBe('/tmp/bv-preclick-123.png');
    expect(result.post_click_screenshot_path).toBe('/tmp/bv-postclick-123.png');
    expect(result.grid).toEqual({
      precision: 48,
      cols: 20,
      rows: 20,
      cell_width_px: 48,
      cell_height_px: 48,
      viewport: { width: 960, height: 960 },
    });
    expect(capture.click).toHaveBeenCalledWith(24, 24);
    expect(capture.waitForMs).toHaveBeenCalledWith(300);
    expect(capture.logAction).toHaveBeenCalledWith(
      expect.objectContaining({ step: 'pre_click_screenshot' })
    );
    expect(capture.logAction).toHaveBeenCalledWith(
      expect.objectContaining({ step: 'execute' })
    );
    expect(capture.logAction).toHaveBeenCalledWith(
      expect.objectContaining({ step: 'post_click_screenshot' })
    );
  });

  test('out-of-bounds col20 row0 precision48 returns COORD_OUT_OF_BOUNDS and capture.click NOT called', async () => {
    const capture = makeCapture();

    const result = await click.handler(
      { col: 20, row: 0, precision: 48 },
      () => capture
    );

    expect(result.error).toBe('COORD_OUT_OF_BOUNDS');
    expect(capture.click).not.toHaveBeenCalled();
    expect(capture.screenshot).not.toHaveBeenCalled();
  });

  test('NO_PAGE when hasPage false', async () => {
    const capture = makeCapture({ hasPage: false });

    const result = await click.handler(
      { col: 0, row: 0, precision: 48 },
      () => capture
    );

    expect(result).toEqual({ error: 'NO_PAGE', detail: 'Call view first' });
    expect(capture.click).not.toHaveBeenCalled();
  });

  test('precision 0 returns INVALID_PRECISION', async () => {
    const capture = makeCapture();

    const result = await click.handler(
      { col: 0, row: 0, precision: 0 },
      () => capture
    );

    expect(result.error).toBe('INVALID_PRECISION');
    expect(capture.click).not.toHaveBeenCalled();
  });

  test('precision 1.5 returns INVALID_PRECISION', async () => {
    const capture = makeCapture();

    const result = await click.handler(
      { col: 0, row: 0, precision: 1.5 },
      () => capture
    );

    expect(result.error).toBe('INVALID_PRECISION');
    expect(capture.click).not.toHaveBeenCalled();
  });

  test('precision -3 returns INVALID_PRECISION', async () => {
    const capture = makeCapture();

    const result = await click.handler(
      { col: 0, row: 0, precision: -3 },
      () => capture
    );

    expect(result.error).toBe('INVALID_PRECISION');
    expect(capture.click).not.toHaveBeenCalled();
  });

  test('precision 1 col0 row0 succeeds', async () => {
    const capture = makeCapture();

    const result = await click.handler(
      { col: 0, row: 0, precision: 1 },
      () => capture
    );

    expect(result.clicked_at).toEqual({ x: 0.5, y: 0.5 });
    expect(result.grid.cols).toBe(960);
    expect(result.grid.rows).toBe(960);
    expect(capture.click).toHaveBeenCalledWith(0.5, 0.5);
  });

  test('precision 960 col0 row0 succeeds', async () => {
    const capture = makeCapture();

    const result = await click.handler(
      { col: 0, row: 0, precision: 960 },
      () => capture
    );

    expect(result.clicked_at).toEqual({ x: 480, y: 480 });
    expect(result.grid.cols).toBe(1);
    expect(result.grid.rows).toBe(1);
    expect(capture.click).toHaveBeenCalledWith(480, 480);
  });
});
