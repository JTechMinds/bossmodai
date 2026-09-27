const scroll = require('../tools/scroll');

describe('scroll tool', () => {
  function makeCapture(overrides = {}) {
    return {
      hasPage: true,
      screenshot: jest.fn().mockResolvedValue('/tmp/bv-postscroll-123.png'),
      click: jest.fn(),
      scroll: jest.fn(),
      logAction: jest.fn(),
      waitForMs: jest.fn(),
      ...overrides,
    };
  }

  test('precision 12 returns PRECISION_NOT_ALLOWED', async () => {
    const capture = makeCapture();
    const result = await scroll.handler({
      col: 0,
      row: 0,
      precision: 12,
      direction: 'down',
    }, () => capture);

    expect(result.error).toBe('PRECISION_NOT_ALLOWED');
    expect(capture.scroll).not.toHaveBeenCalled();
  });

  test('precision 24 succeeds at boundary', async () => {
    const capture = makeCapture();
    const result = await scroll.handler({
      col: 0,
      row: 0,
      precision: 24,
      direction: 'down',
    }, () => capture);

    expect(result.error).toBeUndefined();
    expect(capture.scroll).toHaveBeenCalled();
    expect(capture.waitForMs).toHaveBeenCalledWith(300);
  });

  test('precision 960 succeeds at edge', async () => {
    const capture = makeCapture();
    const result = await scroll.handler({
      col: 0,
      row: 0,
      precision: 960,
      direction: 'down',
    }, () => capture);

    expect(result.error).toBeUndefined();
    expect(capture.scroll).toHaveBeenCalled();
  });

  test('precision 0 returns INVALID_PRECISION', async () => {
    const capture = makeCapture();
    const result = await scroll.handler({
      col: 0,
      row: 0,
      precision: 0,
      direction: 'down',
    }, () => capture);

    expect(result.error).toBe('INVALID_PRECISION');
    expect(capture.scroll).not.toHaveBeenCalled();
  });

  test('scroll down amount 3 precision 48 produces delta y 144', async () => {
    const capture = makeCapture();
    const result = await scroll.handler({
      col: 0,
      row: 0,
      precision: 48,
      direction: 'down',
      amount: 3,
    }, () => capture);

    expect(result.delta).toEqual({ x: 0, y: 144 });
    expect(capture.scroll).toHaveBeenCalledWith(0, 144);
  });

  test('scroll up amount 2 precision 96 produces delta y -192', async () => {
    const capture = makeCapture();
    const result = await scroll.handler({
      col: 0,
      row: 0,
      precision: 96,
      direction: 'up',
      amount: 2,
    }, () => capture);

    expect(result.delta).toEqual({ x: 0, y: -192 });
    expect(capture.scroll).toHaveBeenCalledWith(0, -192);
  });

  test('scroll left amount 1 precision 48 produces delta x -48', async () => {
    const capture = makeCapture();
    const result = await scroll.handler({
      col: 0,
      row: 0,
      precision: 48,
      direction: 'left',
      amount: 1,
    }, () => capture);

    expect(result.delta).toEqual({ x: -48, y: 0 });
    expect(capture.scroll).toHaveBeenCalledWith(-48, 0);
  });

  test('scroll right amount 1 precision 48 produces delta x 48', async () => {
    const capture = makeCapture();
    const result = await scroll.handler({
      col: 0,
      row: 0,
      precision: 48,
      direction: 'right',
      amount: 1,
    }, () => capture);

    expect(result.delta).toEqual({ x: 48, y: 0 });
    expect(capture.scroll).toHaveBeenCalledWith(48, 0);
  });

  test('NO_PAGE when hasPage is false', async () => {
    const capture = makeCapture({ hasPage: false });
    const result = await scroll.handler({
      col: 0,
      row: 0,
      precision: 48,
      direction: 'down',
    }, () => capture);

    expect(result.error).toBe('NO_PAGE');
    expect(capture.scroll).not.toHaveBeenCalled();
  });

  test('successful result includes scrolled_at, delta, post_scroll_screenshot_path, and grid', async () => {
    const capture = makeCapture();
    const result = await scroll.handler({
      col: 1,
      row: 2,
      precision: 48,
      direction: 'down',
    }, () => capture);

    expect(result.scrolled_at).toEqual({ x: 72, y: 120 });
    expect(result.delta).toEqual({ x: 0, y: 144 });
    expect(result.post_scroll_screenshot_path).toBe('/tmp/bv-postscroll-123.png');
    expect(result.grid).toEqual({
      precision: 48,
      cols: 20,
      rows: 20,
      cell_width_px: 48,
      cell_height_px: 48,
      viewport: { width: 960, height: 960 },
    });
  });
});
