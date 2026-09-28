'use strict';

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');
const { healthCheck, _instance, VIEWPORT } = require('../capture/headless');

const DESKTOP_VIEWPORT = Object.freeze({
  width: 960,
  height: 768,
  colorDepth: 24
});

const EVIDENCE_DIR = path.resolve(__dirname, '../../evidence/m2/capture');

function validateDesktopPrecision(N) {
  if (N === undefined || N === null) {
    N = 48;
  }
  if (typeof N !== 'number' || !Number.isInteger(N)) {
    return { valid: false, error: 'INVALID_PRECISION', detail: 'precision must be a positive integer 1-768; got ' + N };
  }
  if (N < 1 || N > 768) {
    return { valid: false, error: 'INVALID_PRECISION', detail: 'precision must be a positive integer 1-768; got ' + N };
  }
  const cols = Math.floor(960 / N);
  const rows = Math.floor(768 / N);
  return { valid: true, cols, rows, cell_width_px: N, cell_height_px: N };
}

function buildGridMetadata(precision) {
  const cols = Math.floor(960 / precision);
  const rows = Math.floor(768 / precision);
  return {
    precision,
    cols,
    rows,
    cell_width_px: precision,
    cell_height_px: precision,
    viewport: { width: 960, height: 768 }
  };
}

async function captureDesktop(options) {
  const opts = options || {};
  const precision = opts.precision !== undefined ? opts.precision : 48;
  const evidence = opts.evidence === true;

  // Step 1: Check browser reachability
  const hc = healthCheck();
  if (!hc.healthy) {
    return { error: 'BROWSER_NOT_RUNNING', detail: 'Playwright headless Chromium is not reachable' };
  }

  // Step 2: Validate precision
  const prec = validateDesktopPrecision(precision);
  if (!prec.valid) {
    return { error: 'INVALID_PRECISION', detail: prec.detail };
  }

  // Step 3: Generate output path
  const OUT_PATH = '/tmp/bv-desktop-' + Date.now() + '.png';

  // Step 4: Capture viewport screenshot
  try {
    await _instance.page.screenshot({ path: OUT_PATH, type: 'png' });
  } catch (e) {
    return { error: 'CAPTURE_FAILED', detail: 'Playwright page.screenshot() failed: ' + e.message };
  }

  // Step 5: Verify PNG exists
  if (!fs.existsSync(OUT_PATH)) {
    return { error: 'CAPTURE_FAILED', detail: 'output file not found after capture: ' + OUT_PATH };
  }

  // Step 6: Verify dimensions
  let width, height;
  try {
    const identifyOut = execSync(`identify -format "%w %h" "${OUT_PATH}"`, { encoding: 'utf-8' }).trim();
    const parts = identifyOut.split(' ');
    width = parseInt(parts[0], 10);
    height = parseInt(parts[1], 10);
  } catch (e) {
    const buf = fs.readFileSync(OUT_PATH);
    width = buf.readUInt32BE(16);
    height = buf.readUInt32BE(20);
  }
  if (width !== 960 || height !== 768) {
    return { error: 'CAPTURE_RESOLUTION_MISMATCH', detail: 'expected 960x768, got ' + width + 'x' + height };
  }

  // Step 7: Evidence copy (conditional)
  if (evidence) {
    if (!fs.existsSync(EVIDENCE_DIR)) {
      fs.mkdirSync(EVIDENCE_DIR, { recursive: true });
    }
    const evidencePath = path.join(EVIDENCE_DIR, 'desktop-' + Date.now() + '.png');
    fs.copyFileSync(OUT_PATH, evidencePath);
  }

  // Step 8: Return success result
  return {
    source: 'desktop',
    screenshot_path: OUT_PATH,
    screenshot_width: 960,
    screenshot_height: 768,
    grid: buildGridMetadata(precision),
    timestamp: new Date().toISOString()
  };
}

module.exports = {
  DESKTOP_VIEWPORT,
  validateDesktopPrecision,
  buildGridMetadata,
  captureDesktop
};
