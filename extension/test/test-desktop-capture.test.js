'use strict';

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const { startBrowser, stopBrowser, healthCheck, _instance } = require('../capture/headless');
const { DESKTOP_VIEWPORT, validateDesktopPrecision, buildGridMetadata, captureDesktop } = require('../desktop/capture');
const view = require('../tools/view');

const CONFIG_PATH = path.resolve(__dirname, '../../toolkit.config.json');
const TEST_IMAGE = path.resolve(__dirname, '../../test_assets/desktop_target_960x768.png');
const TEST_IMAGE_URL = 'file://' + TEST_IMAGE;
const EVIDENCE_DIR = path.resolve(__dirname, '../../evidence/m2/capture');

function ensureTestImage() {
  if (!fs.existsSync(TEST_IMAGE)) {
    fs.mkdirSync(path.dirname(TEST_IMAGE), { recursive: true });
    execSync(
      `convert -size 960x768 xc:'#101010' -fill '#FF0000' -draw 'rectangle 48,48 95,95' ` +
      `-fill '#00FF00' -draw 'rectangle 480,384 527,431' -fill '#0000FF' -draw 'rectangle 912,720 959,767' ` +
      `"${TEST_IMAGE}"`,
      { encoding: 'utf-8' }
    );
  }
}

async function loadTestImage() {
  await _instance.page.goto(TEST_IMAGE_URL, { waitUntil: 'networkidle', timeout: 5000 });
  await _instance.page.waitForTimeout(300);
}

function samplePixel(pngPath, x, y) {
  const out = execSync(`convert "${pngPath}" -crop 1x1+${x}+${y} txt:-`, { encoding: 'utf-8' }).trim();
  const match = out.match(/#([0-9A-Fa-f]{6})/);
  return match ? '#' + match[1].toUpperCase() : null;
}

function setConfigEnabled(enabled) {
  const raw = fs.readFileSync(CONFIG_PATH, 'utf-8');
  const config = JSON.parse(raw);
  config.extensions['browser-vision'].enabled = enabled;
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2) + '\n');
}

function getCapture() {
  return _instance;
}

describe('M2.2 Desktop Capture', () => {
  beforeAll(async () => {
    ensureTestImage();
    setConfigEnabled(true);
    await startBrowser();
  });

  afterAll(async () => {
    setConfigEnabled(false);
    await stopBrowser();
  });

  // --- Tests 1-3: capture at various precisions ---

  test('captures desktop at default precision', async () => {
    await loadTestImage();
    const result = await captureDesktop({ precision: 48 });
    expect(result.error).toBeUndefined();
    expect(result.source).toBe('desktop');
    expect(result.screenshot_width).toBe(960);
    expect(result.screenshot_height).toBe(768);
    expect(result.grid.cols).toBe(20);
    expect(result.grid.rows).toBe(16);
  });

  test('captures desktop at precision 96', async () => {
    await loadTestImage();
    const result = await captureDesktop({ precision: 96 });
    expect(result.error).toBeUndefined();
    expect(result.grid.cols).toBe(10);
    expect(result.grid.rows).toBe(8);
  });

  test('captures desktop at precision 24', async () => {
    await loadTestImage();
    const result = await captureDesktop({ precision: 24 });
    expect(result.error).toBeUndefined();
    expect(result.grid.cols).toBe(40);
    expect(result.grid.rows).toBe(32);
  });

  // --- Tests 4-9: precision validation ---

  test('rejects precision 0', async () => {
    const result = await captureDesktop({ precision: 0 });
    expect(result.error).toBe('INVALID_PRECISION');
  });

  test('rejects precision -1', async () => {
    const result = await captureDesktop({ precision: -1 });
    expect(result.error).toBe('INVALID_PRECISION');
  });

  test('rejects precision 48.5', async () => {
    const result = await captureDesktop({ precision: 48.5 });
    expect(result.error).toBe('INVALID_PRECISION');
  });

  test('rejects precision 769', async () => {
    const result = await captureDesktop({ precision: 769 });
    expect(result.error).toBe('INVALID_PRECISION');
  });

  test('rejects precision 960', async () => {
    const result = await captureDesktop({ precision: 960 });
    expect(result.error).toBe('INVALID_PRECISION');
  });

  test('rejects string precision', async () => {
    const result = await captureDesktop({ precision: '48' });
    expect(result.error).toBe('INVALID_PRECISION');
  });

  // --- Test 10: BROWSER_NOT_RUNNING ---

  test('returns BROWSER_NOT_RUNNING when browser is stopped', async () => {
    await stopBrowser();
    const result = await captureDesktop({ precision: 48 });
    expect(result.error).toBe('BROWSER_NOT_RUNNING');
    await startBrowser();
  });

  // --- Test 11: url rejection ---

  test('rejects url param', async () => {
    await loadTestImage();
    const result = await view.handler({ url: 'https://example.com' }, getCapture);
    expect(result.error).toBe('DESKTOP_URL_UNSUPPORTED');
  });

  // --- Tests 12-14: pixel sampling ---

  test('red target pixel matches', async () => {
    await loadTestImage();
    const result = await captureDesktop({ precision: 48 });
    expect(result.error).toBeUndefined();
    const color = samplePixel(result.screenshot_path, 72, 72);
    expect(color).toBe('#FF0000');
  });

  test('green target pixel matches', async () => {
    await loadTestImage();
    const result = await captureDesktop({ precision: 48 });
    expect(result.error).toBeUndefined();
    const color = samplePixel(result.screenshot_path, 504, 408);
    expect(color).toBe('#00FF00');
  });

  test('blue target pixel matches', async () => {
    await loadTestImage();
    const result = await captureDesktop({ precision: 48 });
    expect(result.error).toBeUndefined();
    const color = samplePixel(result.screenshot_path, 936, 744);
    expect(color).toBe('#0000FF');
  });

  // --- Test 15: evidence copy ---

  test('evidence copy is created when evidence=true', async () => {
    await loadTestImage();
    const result = await captureDesktop({ precision: 48, evidence: true });
    expect(result.error).toBeUndefined();
    const files = fs.readdirSync(EVIDENCE_DIR).filter(f => f.startsWith('desktop-') && f.endsWith('.png'));
    expect(files.length).toBeGreaterThan(0);
  });

  // --- Tests 16-18: extension toggle ---

  test('returns EXTENSION_DISABLED when extension is off', async () => {
    setConfigEnabled(false);
    const result = await view.handler({}, getCapture);
    expect(result.error).toBe('EXTENSION_DISABLED');
  });

  test('succeeds after re-enabling extension', async () => {
    setConfigEnabled(true);
    await loadTestImage();
    const result = await view.handler({ precision: 48 }, getCapture);
    expect(result.error).toBeUndefined();
    expect(result.source).toBe('desktop');
  });

  test('toggle persists in config', async () => {
    setConfigEnabled(true);
    const raw = fs.readFileSync(CONFIG_PATH, 'utf-8');
    const config = JSON.parse(raw);
    expect(config.extensions['browser-vision'].enabled).toBe(true);
    setConfigEnabled(false);
  });
});
