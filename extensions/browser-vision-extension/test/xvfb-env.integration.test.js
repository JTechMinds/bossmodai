const { execSync } = require('child_process');

let xvfbAvailable = false;
let xdpyinfoAvailable = false;

try {
  execSync('which Xvfb', { stdio: 'pipe' });
  xvfbAvailable = true;
} catch {
  xvfbAvailable = false;
}

try {
  execSync('which xdpyinfo', { stdio: 'pipe' });
  xdpyinfoAvailable = true;
} catch {
  xdpyinfoAvailable = false;
}

const describeIntegration = (xvfbAvailable && xdpyinfoAvailable) ? describe : describe.skip;

describeIntegration('xvfb-env integration (real Xvfb)', () => {
  const env = require('../xvfb/env.js');

  jest.setTimeout(30000);

  beforeAll(async () => {
    // Ensure clean state
    await env.stopXvfb();
  });

  afterAll(async () => {
    await env.stopXvfb();
  });

  test('startXvfb() starts a real Xvfb and returns healthy state', async () => {
    const state = await env.startXvfb();
    expect(state.error).toBeUndefined();
    expect(state.running).toBe(true);
    expect(state.display).toBe(':99');
    expect(state.width).toBe(960);
    expect(state.height).toBe(960);
    expect(state.depth).toBe(24);
    expect(state.pid).toBeGreaterThan(0);
    expect(state.started_at).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    expect(state.display_env).toBe(':99');
  });

  test('startXvfb() is idempotent when already running', async () => {
    const s1 = env.getXvfbState();
    const s2 = await env.startXvfb();
    expect(s2.pid).toBe(s1.pid);
    expect(s2.started_at).toBe(s1.started_at);
  });

  test('stopXvfb() stops the real Xvfb process', async () => {
    const state = env.getXvfbState();
    const pid = state.pid;
    const result = await env.stopXvfb();
    expect(result.error).toBeUndefined();
    expect(result.running).toBe(false);
    // Verify process is gone
    let alive = true;
    try {
      process.kill(pid, 0);
    } catch {
      alive = false;
    }
    expect(alive).toBe(false);
  });

  test('stopXvfb() is idempotent when not running', async () => {
    const result = await env.stopXvfb();
    expect(result.running).toBe(false);
  });

  test('restartXvfb() stops then starts with a new PID', async () => {
    await env.startXvfb();
    const oldPid = env.getXvfbState().pid;
    const state = await env.restartXvfb();
    expect(state.error).toBeUndefined();
    expect(state.running).toBe(true);
    expect(state.pid).not.toBe(oldPid);
  });

  test('assertXvfbRunning() returns state when running', () => {
    const state = env.assertXvfbRunning();
    expect(state.running).toBe(true);
    expect(state.display).toBe(':99');
  });

  test('getXvfbState() returns null after stop', async () => {
    await env.stopXvfb();
    expect(env.getXvfbState()).toBeNull();
  });
});
