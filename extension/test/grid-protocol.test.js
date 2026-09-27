const {
  setPrecision,
  validateCoordinate,
  cellCenter,
  gridMetadata
} = require('../grid/protocol');

describe('setPrecision', () => {
  test('N=48 is valid', () => {
    expect(setPrecision(48)).toEqual({
      valid: true,
      cols: 20,
      rows: 20,
      cell_width_px: 48,
      cell_height_px: 48
    });
  });

  test('N=96 is valid', () => {
    expect(setPrecision(96)).toEqual({
      valid: true,
      cols: 10,
      rows: 10,
      cell_width_px: 96,
      cell_height_px: 96
    });
  });

  test('N=1 is valid', () => {
    expect(setPrecision(1)).toEqual({
      valid: true,
      cols: 960,
      rows: 960,
      cell_width_px: 1,
      cell_height_px: 1
    });
  });

  test('N=960 is valid', () => {
    expect(setPrecision(960)).toEqual({
      valid: true,
      cols: 1,
      rows: 1,
      cell_width_px: 960,
      cell_height_px: 960
    });
  });

  test('N=0 is invalid', () => {
    expect(setPrecision(0)).toEqual({
      valid: false,
      error: 'INVALID_PRECISION'
    });
  });

  test('N=-5 is invalid', () => {
    expect(setPrecision(-5)).toEqual({
      valid: false,
      error: 'INVALID_PRECISION'
    });
  });

  test('N=961 is invalid', () => {
    expect(setPrecision(961)).toEqual({
      valid: false,
      error: 'INVALID_PRECISION'
    });
  });

  test('N=48.5 is invalid', () => {
    expect(setPrecision(48.5)).toEqual({
      valid: false,
      error: 'INVALID_PRECISION'
    });
  });

  test("N='48' string is invalid", () => {
    expect(setPrecision('48')).toEqual({
      valid: false,
      error: 'INVALID_PRECISION'
    });
  });
});

describe('validateCoordinate', () => {
  test('(48, 0, 0) is valid', () => {
    expect(validateCoordinate(48, 0, 0)).toEqual({ valid: true });
  });

  test('(48, 19, 19) is valid', () => {
    expect(validateCoordinate(48, 19, 19)).toEqual({ valid: true });
  });

  test('(48, -1, 0) is invalid', () => {
    expect(validateCoordinate(48, -1, 0)).toEqual({
      valid: false,
      error: 'COORD_OUT_OF_BOUNDS'
    });
  });

  test('(48, 20, 0) is invalid', () => {
    expect(validateCoordinate(48, 20, 0)).toEqual({
      valid: false,
      error: 'COORD_OUT_OF_BOUNDS'
    });
  });

  test('(48, 0, 20) is invalid', () => {
    expect(validateCoordinate(48, 0, 20)).toEqual({
      valid: false,
      error: 'COORD_OUT_OF_BOUNDS'
    });
  });

  test('(48, 20, 20) is invalid', () => {
    expect(validateCoordinate(48, 20, 20)).toEqual({
      valid: false,
      error: 'COORD_OUT_OF_BOUNDS'
    });
  });

  test('(1, 959, 959) is valid', () => {
    expect(validateCoordinate(1, 959, 959)).toEqual({ valid: true });
  });

  test('(1, 960, 0) is invalid', () => {
    expect(validateCoordinate(1, 960, 0)).toEqual({
      valid: false,
      error: 'COORD_OUT_OF_BOUNDS'
    });
  });

  test('(960, 0, 0) is valid', () => {
    expect(validateCoordinate(960, 0, 0)).toEqual({ valid: true });
  });

  test('(960, 1, 0) is invalid', () => {
    expect(validateCoordinate(960, 1, 0)).toEqual({
      valid: false,
      error: 'COORD_OUT_OF_BOUNDS'
    });
  });
});

describe('cellCenter', () => {
  test('cellCenter(48, 0, 0)', () => {
    expect(cellCenter(48, 0, 0)).toEqual({ x: 24, y: 24 });
  });

  test('cellCenter(48, 19, 19)', () => {
    expect(cellCenter(48, 19, 19)).toEqual({ x: 936, y: 936 });
  });

  test('cellCenter(96, 0, 0)', () => {
    expect(cellCenter(96, 0, 0)).toEqual({ x: 48, y: 48 });
  });

  test('cellCenter(96, 9, 9)', () => {
    expect(cellCenter(96, 9, 9)).toEqual({ x: 912, y: 912 });
  });

  test('cellCenter(1, 0, 0)', () => {
    expect(cellCenter(1, 0, 0)).toEqual({ x: 0.5, y: 0.5 });
  });

  test('cellCenter(1, 959, 959)', () => {
    expect(cellCenter(1, 959, 959)).toEqual({ x: 959.5, y: 959.5 });
  });

  test('cellCenter(960, 0, 0)', () => {
    expect(cellCenter(960, 0, 0)).toEqual({ x: 480, y: 480 });
  });
});

describe('gridMetadata', () => {
  test('gridMetadata(48)', () => {
    expect(gridMetadata(48)).toEqual({
      precision: 48,
      cols: 20,
      rows: 20,
      cell_width_px: 48,
      cell_height_px: 48,
      viewport: {
        width: 960,
        height: 960
      }
    });
  });

  test('gridMetadata(1)', () => {
    expect(gridMetadata(1)).toEqual({
      precision: 1,
      cols: 960,
      rows: 960,
      cell_width_px: 1,
      cell_height_px: 1,
      viewport: {
        width: 960,
        height: 960
      }
    });
  });

  test('gridMetadata(960)', () => {
    expect(gridMetadata(960)).toEqual({
      precision: 960,
      cols: 1,
      rows: 1,
      cell_width_px: 960,
      cell_height_px: 960,
      viewport: {
        width: 960,
        height: 960
      }
    });
  });
});
