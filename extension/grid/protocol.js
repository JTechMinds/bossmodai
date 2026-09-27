'use strict';

const VIEWPORT = { width: 960, height: 960 };

function setPrecision(N) {
  if (typeof N !== 'number' || !Number.isInteger(N) || N < 1 || N > 960) {
    return { valid: false, error: 'INVALID_PRECISION' };
  }
  const cols = Math.floor(VIEWPORT.width / N);
  const rows = Math.floor(VIEWPORT.height / N);
  return { valid: true, cols, rows, cell_width_px: N, cell_height_px: N };
}

function validateCoordinate(N, col, row) {
  const p = setPrecision(N);
  if (!p.valid) {
    return { valid: false, error: 'INVALID_PRECISION' };
  }
  if (col < 0 || col >= p.cols || row < 0 || row >= p.rows) {
    return { valid: false, error: 'COORD_OUT_OF_BOUNDS' };
  }
  return { valid: true };
}

function cellCenter(N, col, row) {
  return { x: col * N + N / 2, y: row * N + N / 2 };
}

function gridMetadata(N) {
  const p = setPrecision(N);
  if (!p.valid) {
    return { error: 'INVALID_PRECISION' };
  }
  return {
    precision: N,
    cols: p.cols,
    rows: p.rows,
    cell_width_px: N,
    cell_height_px: N,
    viewport: { width: 960, height: 960 },
  };
}

module.exports = { VIEWPORT, setPrecision, validateCoordinate, cellCenter, gridMetadata };
