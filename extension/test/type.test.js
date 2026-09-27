const type = require('../tools/type');

function makeCapture(overrides = {}) {
  return {
    hasPage: true,
    screenshot: jest.fn().mockResolvedValue('/tmp/bv-pretype-123.png'),
    click: jest.fn(),
    type: jest.fn(),
    logAction: jest.fn(),
    waitForMs: jest.fn(),
    ...overrides,
  };
}

describe('type tool', () => {
  test('precision 96 returns PRECISION_NOT_ALLOWED', async () => {
    const capture = makeCapture();
    const result = await type.handler(
      { col: 0, row: 0, precision: 96, text: 'hello' },
      () => capture
    );

    expect(result).toEqual(
      expect.objectContaining({
        error: 'PRECISION_NOT_ALLOWED',
      })
    );
    expect(capture.type).not.toHaveBeenCalled();
  });

  test('precision 48 succeeds at the boundary', async () => {
    const capture = makeCapture();
    const result = await type.handler(
      { col: 0, row: 0, precision: 48, text: 'hello' },
      () => capture
    );

    expect(result.typed_at).toEqual({ x: 24, y: 24 });
    expect(result.text_length).toBe(5);
    expect(result.pre_type_screenshot_path).toBe('/tmp/bv-pretype-123.png');
    expect(result.grid).toEqual(
      expect.objectContaining({
        precision: 48,
        cols: 20,
        rows: 20,
      })
    );
    expect(capture.type).toHaveBeenCalledWith('hello');
  });

  test('precision 1 succeeds at the maximum-density edge', async () => {
    const capture = makeCapture();
    const result = await type.handler(
      { col: 0, row: 0, precision: 1, text: 'x' },
      () => capture
    );

    expect(result.typed_at).toEqual({ x: 0.5, y: 0.5 });
    expect(result.text_length).toBe(1);
    expect(result.grid).toEqual(
      expect.objectContaining({
        precision: 1,
        cols: 960,
        rows: 960,
      })
    );
  });

  test('precision 0 returns INVALID_PRECISION', async () => {
    const capture = makeCapture();
    const result = await type.handler(
      { col: 0, row: 0, precision: 0, text: 'hello' },
      () => capture
    );

    expect(result).toEqual(
      expect.objectContaining({
        error: 'INVALID_PRECISION',
      })
    );
    expect(capture.type).not.toHaveBeenCalled();
  });

  test('precision -1 returns INVALID_PRECISION', async () => {
    const capture = makeCapture();
    const result = await type.handler(
      { col: 0, row: 0, precision: -1, text: 'hello' },
      () => capture
    );

    expect(result).toEqual(
      expect.objectContaining({
        error: 'INVALID_PRECISION',
      })
    );
    expect(capture.type).not.toHaveBeenCalled();
  });

  test('returns NO_PAGE when no page is loaded', async () => {
    const capture = makeCapture({ hasPage: false });
    const result = await type.handler(
      { col: 0, row: 0, precision: 48, text: 'hello' },
      () => capture
    );

    expect(result).toEqual(
      expect.objectContaining({
        error: 'NO_PAGE',
      })
    );
    expect(capture.type).not.toHaveBeenCalled();
  });

  test('out-of-bounds coordinate returns COORD_OUT_OF_BOUNDS', async () => {
    const capture = makeCapture();
    const result = await type.handler(
      { col: 20, row: 0, precision: 48, text: 'hello' },
      () => capture
    );

    expect(result).toEqual(
      expect.objectContaining({
        error: 'COORD_OUT_OF_BOUNDS',
      })
    );
    expect(capture.click).not.toHaveBeenCalled();
    expect(capture.type).not.toHaveBeenCalled();
  });
});
