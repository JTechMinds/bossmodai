'use strict';

const fs = require('fs');
const path = require('path');
const { setPrecision, validateCoordinate, cellCenter, gridMetadata } = require('../grid/protocol');

const CONFIG_PATH = path.resolve(__dirname, '../../toolkit.config.json');

function isExtensionEnabled() {
  try {
    const raw = fs.readFileSync(CONFIG_PATH, 'utf-8');
    const config = JSON.parse(raw);
    return config.extensions && config.extensions['browser-vision'] && config.extensions['browser-vision'].enabled === true;
  } catch (e) {
    return false;
  }
}

module.exports = {
  name: 'scroll',
  description: 'Scroll the page at a grid cell coordinate',
  params: {
    col: { type: 'integer', required: true },
    row: { type: 'integer', required: true },
    precision: {
      type: 'integer',
      required: true,
      description: 'Grid spacing in px. Must be 24-960 for scroll (coarse/medium-grained grids only).'
    },
    direction: {
      type: 'string',
      enum: ['up', 'down', 'left', 'right'],
      required: true
    },
    amount: {
      type: 'integer',
      required: false,
      default: 3,
      description: 'Number of scroll steps (each step = 1 cell height/width in px)'
    }
  },
  async handler(params, getCapture) {
    if (!isExtensionEnabled()) {
      return { error: 'EXTENSION_DISABLED', detail: 'browser-vision extension is disabled; enable it in toolkit settings' };
    }

    const N = params.precision;
    const prec = setPrecision(N);
    if (!prec.valid) {
      return { error: 'INVALID_PRECISION', detail: 'precision must be a positive integer 1-960; got ' + N };
    }

    if (N < 24) {
      return { error: 'PRECISION_NOT_ALLOWED', detail: 'scroll requires precision >= 24 (coarse/medium); got ' + N };
    }

    const vc = validateCoordinate(N, params.col, params.row);
    if (!vc.valid) {
      return { error: vc.error, detail: 'col ' + params.col + ' exceeds ' + prec.cols + ' cols for precision ' + N };
    }

    const cap = getCapture();
    if (!cap || !cap.hasPage) {
      return { error: 'NO_PAGE', detail: 'Call view first' };
    }

    const amount = params.amount !== undefined ? params.amount : 3;
    const center = cellCenter(N, params.col, params.row);

    let dx = 0;
    let dy = 0;
    if (params.direction === 'down') {
      dy = amount * N;
    } else if (params.direction === 'up') {
      dy = -amount * N;
    } else if (params.direction === 'right') {
      dx = amount * N;
    } else if (params.direction === 'left') {
      dx = -amount * N;
    }

    await cap.click(center.x, center.y);
    await cap.scroll(dx, dy);
    await cap.waitForMs(300);

    const postShot = await cap.screenshot('postscroll');

    return {
      scrolled_at: center,
      delta: { x: dx, y: dy },
      post_scroll_screenshot_path: postShot,
      grid: gridMetadata(N),
      timestamp: new Date().toISOString()
    };
  }
};
