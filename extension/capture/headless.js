const { chromium } = require('playwright');
const path = require('path');
const os = require('os');
const fs = require('fs');

const ACTION_LOG = '/tmp/bv-action-log.jsonl';

const VIEWPORT = Object.freeze({
  width: 960,
  height: 768
});

class HeadlessCapture {
  constructor() {
    this.browser = null;
    this.page = null;
  }

  async init() {
    this.browser = await chromium.launch({ headless: true });
    this.page = await this.browser.newPage();
    await this.page.setViewportSize({ width: VIEWPORT.width, height: VIEWPORT.height });
  }

  async navigate(url) {
    await this.page.goto(url, {
      waitUntil: 'networkidle',
      timeout: 10000,
    });
  }

  async screenshot(prefix) {
    const filePath = path.join(os.tmpdir(), `bv-${prefix}-${Date.now()}.png`);
    await this.page.screenshot({ path: filePath });
    return filePath;
  }

  async click(x, y) {
    await this.page.mouse.click(x, y);
  }

  async type(text) {
    await this.page.keyboard.type(text);
  }

  async scroll(deltaX, deltaY) {
    await this.page.mouse.wheel(deltaX, deltaY);
  }

  async waitForMs(ms) {
    await new Promise((resolve) => setTimeout(resolve, ms));
  }

  get hasPage() {
    return this.page !== null;
  }

  get pageUrl() {
    return this.page ? this.page.url() : null;
  }

  async close() {
    if (this.browser) {
      await this.browser.close();
      this.browser = null;
      this.page = null;
    }
  }

  logAction(entry) {
    const line = JSON.stringify({ ts: new Date().toISOString(), ...entry }) + '\n';
    fs.appendFileSync(ACTION_LOG, line);
  }
}

// Module-level singleton for lifecycle functions
const _instance = new HeadlessCapture();

async function startBrowser() {
  if (_instance.hasPage) return;
  await _instance.init();
}

async function stopBrowser() {
  if (!_instance.hasPage) return;
  await _instance.close();
}

function healthCheck() {
  return { healthy: _instance.hasPage };
}

module.exports = { HeadlessCapture, ACTION_LOG, VIEWPORT, startBrowser, stopBrowser, healthCheck, _instance };
