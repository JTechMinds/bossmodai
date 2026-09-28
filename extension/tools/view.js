'use strict';

const { setPrecision, gridMetadata } = require('../grid/protocol');

const view = {
  name: 'view',
  description: 'Load a page and capture a screenshot with coordinate-grid metadata',
  params: {
    url: {
      type: 'string',
      required: true,
      description: 'Page URL to load and capture'
    },
    precision: {
      type: 'integer',
      required: false,
      default: 48,
      description: 'Grid spacing in px (cell size). Must be a positive integer 1-960.'
    }
  },
  async handler(params, getCapture) {
    const N = params.precision !== undefined ? params.precision : 48;
    const prec = setPrecision(N);
    if (!prec.valid) {
      return { error: 'INVALID_PRECISION', detail: 'precision must be a positive integer 1-960; got ' + N };
    }
    const cap = getCapture();
    if (!cap) {
      return { error: 'NO_PAGE', detail: 'Call view first' };
    }
    try {
      await cap.navigate(params.url);
    } catch (e) {
      return { error: 'NAV_TIMEOUT', detail: e.message };
    }
    const shot = await cap.screenshot('capture');
    return {
      screenshot_path: shot,
      screenshot_width: 960,
      screenshot_height: 960,
      grid: gridMetadata(N),
      url: cap.pageUrl,
      timestamp: new Date().toISOString()
    };
  }
};

module.exports = view;
