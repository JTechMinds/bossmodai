const { spawn, execSync } = require('child_process');

// Locked constants per TDD §2.1
const XVFB_DISPLAY = ':99';
const XVFB_WIDTH = 960;
const XVFB_HEIGHT = 960;
const XVFB_DEPTH = 24;
const XVFB_START_TIMEOUT_MS = 5000;
const XVFB_STOP_TIMEOUT_MS = 5000;
const XVFB_POLL_MS = 100;
const SIGKILL_WAIT_MS = 1000;

// Module-level state
let pid = null;
let startedAt = null;
let running = false;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function checkBinary(name) {
  try {
    execSync(`command -v ${name}`, { stdio: 'pipe' });
    return true;
  } catch {
    return false;
  }
}

function isProcessAlive(p) {
  try {
    process.kill(p, 0);
    return true;
  } catch {
    return false;
  }
}

function healthCheck() {
  return new Promise((resolve) => {
    const child = spawn('xdpyinfo', ['-display', XVFB_DISPLAY], { stdio: 'pipe' });
    child.on('error', (err) => {
      if (err.code === 'ENOENT') {
        resolve({ healthy: false, missing: true });
      } else {
        resolve({ healthy: false });
      }
    });
    child.on('close', (code) => {
      resolve({ healthy: code === 0 });
    });
  });
}

async function pollHealth(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = await healthCheck();
    if (result.missing) {
      return { ok: false, missing: true };
    }
    if (result.healthy) {
      return { ok: true };
    }
    if (pid && !isProcessAlive(pid)) {
      return { ok: false, processExited: true };
    }
    await sleep(XVFB_POLL_MS);
  }
  return { ok: false, timeout: true };
}

async function startXvfb() {
  // Idempotent: if already running and healthy, return existing state
  if (running && pid && isProcessAlive(pid)) {
    return getXvfbState();
  }

  // Check if display :99 is in use by a non-owned process
  const hc = await healthCheck();
  if (hc.healthy) {
    return { error: 'XVFB_DISPLAY_IN_USE', detail: 'Display :99 is already in use by a non-owned process' };
  }
  if (hc.missing) {
    return { error: 'XVFB_HEALTH_TOOL_MISSING', detail: 'xdpyinfo binary not found' };
  }

  // Check Xvfb binary availability
  if (!checkBinary('Xvfb')) {
    return { error: 'XVFB_DEPENDENCY_MISSING', detail: 'Xvfb binary not found' };
  }

  // Spawn Xvfb
  let stderr = '';
  const child = spawn(
    'Xvfb',
    [':99', '-screen', '0', '960x960x24', '-nolisten', 'tcp', '-ac'],
    { stdio: ['ignore', 'pipe', 'pipe'] }
  );

  child.stderr.on('data', (d) => { stderr += d.toString(); });

  child.on('error', (err) => {
    if (err.code === 'ENOENT') {
      pid = null;
      running = false;
    }
  });

  pid = child.pid;
  startedAt = new Date().toISOString();
  running = true;

  // Poll health
  const result = await pollHealth(XVFB_START_TIMEOUT_MS);

  if (result.missing) {
    try { process.kill(pid, 'SIGKILL'); } catch (e) { /* ignore */ }
    pid = null;
    running = false;
    return { error: 'XVFB_HEALTH_TOOL_MISSING', detail: 'xdpyinfo binary not found' };
  }

  if (result.processExited) {
    pid = null;
    running = false;
    return { error: 'XVFB_START_FAILED', detail: `Xvfb process exited before becoming healthy. stderr: ${stderr}` };
  }

  if (result.timeout) {
    try { process.kill(pid, 'SIGKILL'); } catch (e) { /* ignore */ }
    pid = null;
    running = false;
    return { error: 'XVFB_START_TIMEOUT', detail: 'Xvfb :99 did not become healthy within 5000 ms' };
  }

  return getXvfbState();
}

async function stopXvfb() {
  if (!pid || !running) {
    return { running: false };
  }

  const p = pid;

  // Send SIGTERM
  try { process.kill(p, 'SIGTERM'); } catch (e) { /* ignore */ }

  // Poll for exit
  const deadline = Date.now() + XVFB_STOP_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (!isProcessAlive(p)) {
      pid = null;
      startedAt = null;
      running = false;
      return { running: false };
    }
    await sleep(XVFB_POLL_MS);
  }

  // SIGKILL after timeout
  try { process.kill(p, 'SIGKILL'); } catch (e) { /* ignore */ }

  // Wait up to 1000 ms for SIGKILL exit
  const killDeadline = Date.now() + SIGKILL_WAIT_MS;
  while (Date.now() < killDeadline) {
    if (!isProcessAlive(p)) {
      pid = null;
      startedAt = null;
      running = false;
      return { running: false };
    }
    await sleep(50);
  }

  return { error: 'XVFB_STOP_TIMEOUT', detail: 'Xvfb process did not exit within 5000 ms after stop request' };
}

async function restartXvfb() {
  const stopResult = await stopXvfb();
  if (stopResult.error) return stopResult;
  return startXvfb();
}

function getXvfbState() {
  if (!running || !pid) return null;
  if (!isProcessAlive(pid)) {
    running = false;
    return null;
  }
  return {
    running: true,
    display: XVFB_DISPLAY,
    pid: pid,
    width: XVFB_WIDTH,
    height: XVFB_HEIGHT,
    depth: XVFB_DEPTH,
    started_at: startedAt,
    display_env: XVFB_DISPLAY,
  };
}

function assertXvfbRunning() {
  const state = getXvfbState();
  if (!state) {
    const err = new Error('Xvfb display :99 is not running');
    err.code = 'XVFB_NOT_RUNNING';
    err.result = { error: 'XVFB_NOT_RUNNING', detail: 'Xvfb display :99 is not running' };
    throw err;
  }
  return state;
}

module.exports = {
  startXvfb,
  stopXvfb,
  restartXvfb,
  getXvfbState,
  assertXvfbRunning,
  _constants: { XVFB_DISPLAY, XVFB_WIDTH, XVFB_HEIGHT, XVFB_DEPTH, XVFB_START_TIMEOUT_MS, XVFB_STOP_TIMEOUT_MS, XVFB_POLL_MS },
  _reset: () => { pid = null; startedAt = null; running = false; },
  _setPid: (p) => { pid = p; },
  _setRunning: (r) => { running = r; },
  _setStartedAt: (t) => { startedAt = t; },
};
