'use strict';

const fs = require('fs');
const path = require('path');
const { captureDesktop } = require('../desktop/capture');

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

const view = {
  name: 'view',
  description: 'Capture the Playwright headless Chromium desktop viewport with coordinate-grid metadata',
  params: {
    precision: {
      type: 'integer',
      required: false,
      default: 48,
      description: 'Grid spacing in px (cell size). Must be a positive integer 1-768.'
    },
    url: {
      type: 'string',
      required: false,
      description: 'REJECTED. Presence triggers DESKTOP_URL_UNSUPPORTED.'
    }
  },
  async handler(params, getCapture) {
    // Step 1: Check extension enabled flag
    if (!isExtensionEnabled()) {
      return { error: 'EXTENSION_DISABLED', detail: 'browser-vision extension is disabled; enable it in toolkit settings' };
    }

    // Step 2: Reject url param
    if (params && params.url !== undefined && params.url !== null) {
      return { error: 'DESKTOP_URL_UNSUPPORTED', detail: 'M2 view captures the desktop viewport; url is not accepted' };
    }

    // Step 3: Call captureDesktop
    const precision = params && params.precision !== undefined ? params.precision : 48;
    const result = await captureDesktop({ precision: precision, evidence: false });

    // Step 4: Return result (pass through errors)
    return result;
  }
};

module.exports = view;
