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
  name: 'type',
  description: 'Type text into a grid cell coordinate on the current page',
  params: {
    col: { type: 'integer', required: true },
    row: { type: 'integer', required: true },
    precision: {
      type: 'integer',
      required: true,
      description: 'Grid spacing in px. Must be 1-48 for type (fine-grained grids only).'
    },
    text: { type: 'string', required: true }
  },
  async handler(params, getCapture) {
    if (!isExtensionEnabled()) {
      return { error: 'EXTENSION_DISABLED', detail: 'browser-vision extension is disabled; enable it in toolkit settings' };
    }

    const N = params.precision;
    const prec = setPrecision(N);

    if (!prec.valid) {
      return {
        error: 'INVALID_PRECISION',
        detail: `precision must be a positive integer 1-960; got ${N}`
      };
    }

    if (N > 48) {
      return {
        error: 'PRECISION_NOT_ALLOWED',
        detail: `type requires precision <= 48 (fine-grained); got ${N}`
      };
    }

    const vc = validateCoordinate(N, params.col, params.row);
    if (!vc.valid) {
      return {
        error: vc.error,
        detail: `col ${params.col} exceeds ${prec.cols} cols for precision ${N}`
      };
    }

    const cap = getCapture();
    if (!cap || !cap.hasPage || cap.pageUrl === 'about:blank') {
      return { error: 'NO_PAGE', detail: 'Call view first' };
    }

    let preShot;
    try {
      preShot = await cap.screenshot('pretype');
    } catch (e) {
      return { error: 'PRETYPE_CAPTURE_FAILED', detail: e.message };
    }

    const center = cellCenter(N, params.col, params.row);
    await cap.click(center.x, center.y);
    await cap.waitForMs(150);
    await cap.type(params.text);

    return {
      typed_at: center,
      text_length: params.text.length,
      pre_type_screenshot_path: preShot,
      grid: gridMetadata(N),
      timestamp: new Date().toISOString()
    };
  }
};
