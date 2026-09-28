const { EventEmitter } = require('events');

jest.mock('child_process', () => ({
  spawn: jest.fn(),
  execSync: jest.fn(),
}));

const { spawn, execSync } = require('child_process');
const env = require('../xvfb/env.js');

jest.setTimeout(30000);

let alive;
let termKills;
let killKills;
let signals;
let xvfbCount;
let healthQueue;
let healthFactory;
let xvfbExitImmediately;
let killSpy;

function makeHealthChild(exitCode) {
  const c = new EventEmitter();
  process.nextTick(() => c.emit('close', exitCode));
  return c;
}

function makeHealthError(errCode) {
  const c = new EventEmitter();
  process.nextTick(() => c.emit('error', { code: errCode }));
  return c;
}

beforeEach(() => {
  env._reset();
  spawn.mockReset();
  execSync.mockReset();

  alive = new Map();
  termKills = new Set();
  killKills = new Set();
  signals = [];
  xvfbCount = 0;
  healthQueue = [];
  xvfbExitImmediately = false;

  healthFactory = () => {
    const code = healthQueue.length > 0 ? healthQueue.shift() : 1;
    return makeHealthChild(code);
  };

  execSync.mockImplementation((cmd) => {
    const s = String(cmd);
    if (s.includes('Xvfb')) return '/usr/bin/Xvfb';
    if (s.includes('xdpyinfo')) return '/usr/bin/xdpyinfo';
    return '/bin/true';
  });

  spawn.mockImplementation((cmd) => {
    if (cmd === 'xdpyinfo') {
      return healthFactory();
    }

    if (cmd === 'Xvfb') {
      xvfbCount += 1;
      const pid = 10000 + xvfbCount;
      const child = new EventEmitter();
      child.pid = pid;
      child.stderr = new EventEmitter();
      alive.set(pid, !xvfbExitImmediately);
      return child;
    }

    return new EventEmitter();
  });

  killSpy = jest.spyOn(process, 'kill').mockImplementation((pid, signal) => {
    if (signal === 0) {
      if (!alive.has(pid) || alive.get(pid) === false) {
        const err = new Error('kill ESRCH');
        err.code = 'ESRCH';
        throw err;
      }
      return undefined;
    }

    signals.push(signal);

    if (signal === 'SIGTERM' && termKills.has(pid)) {
      alive.set(pid, false);
    }

    if (signal === 'SIGKILL' && killKills.has(pid)) {
      alive.set(pid, false);
    }

    return true;
  });
});

afterEach(() => {
  if (killSpy) {
    killSpy.mockRestore();
  }
});

async function startHealthy() {
  healthQueue.push(1, 0);
  return env.startXvfb();
}

describe('startXvfb()', () => {
  test('returns healthy XvfbState after successful start', async () => {
    const result = await startHealthy();

    expect(result.running).toBe(true);
    expect(result.display).toBe(':99');
    expect(result.pid).toBeGreaterThan(0);
    expect(result.width).toBe(960);
    expect(result.height).toBe(960);
    expect(result.depth).toBe(24);
    expect(typeof result.started_at).toBe('string');
    expect(result.display_env).toBe(':99');
    expect(xvfbCount).toBe(1);
  });

  test('is idempotent when already running and healthy', async () => {
    const first = await startHealthy();
    const spawnCountAfterFirst = xvfbCount;

    const second = await env.startXvfb();

    expect(second.running).toBe(true);
    expect(second.pid).toBe(first.pid);
    expect(xvfbCount).toBe(spawnCountAfterFirst);
  });

  test('returns XVFB_DISPLAY_IN_USE when display occupied by non-owned process', async () => {
    healthQueue.push(0);

    const result = await env.startXvfb();

    expect(result).toEqual({
      error: 'XVFB_DISPLAY_IN_USE',
      detail: 'Display :99 is already in use by a non-owned process',
    });
    expect(xvfbCount).toBe(0);
  });

  test('returns XVFB_DEPENDENCY_MISSING when Xvfb is missing', async () => {
    healthQueue.push(1);
    execSync.mockImplementation((cmd) => {
      const s = String(cmd);
      if (s.includes('Xvfb')) {
        throw new Error('command not found');
      }
      return '/usr/bin/xdpyinfo';
    });

    const result = await env.startXvfb();

    expect(result.error).toBe('XVFB_DEPENDENCY_MISSING');
    expect(xvfbCount).toBe(0);
  });

  test('returns XVFB_HEALTH_TOOL_MISSING when xdpyinfo is missing', async () => {
    healthFactory = () => makeHealthError('ENOENT');

    const result = await env.startXvfb();

    expect(result.error).toBe('XVFB_HEALTH_TOOL_MISSING');
    expect(xvfbCount).toBe(0);
  });

  test('returns XVFB_START_TIMEOUT when health never succeeds', async () => {
    healthQueue.push(1);

    const result = await env.startXvfb();

    expect(result.error).toBe('XVFB_START_TIMEOUT');
    expect(env.getXvfbState()).toBeNull();
  }, 30000);

  test('returns XVFB_START_FAILED when Xvfb process exits before healthy', async () => {
    healthQueue.push(1);
    xvfbExitImmediately = true;

    const result = await env.startXvfb();

    expect(result.error).toBe('XVFB_START_FAILED');
    expect(env.getXvfbState()).toBeNull();
  });
});

describe('stopXvfb()', () => {
  test('is idempotent when not running', async () => {
    const result = await env.stopXvfb();

    expect(result).toEqual({ running: false });
    expect(signals).toEqual([]);
  });

  test('stops cleanly when process exits after SIGTERM', async () => {
    const started = await startHealthy();
    termKills.add(started.pid);

    const result = await env.stopXvfb();

    expect(result).toEqual({ running: false });
    expect(signals).toEqual(['SIGTERM']);
    expect(env.getXvfbState()).toBeNull();
  });

  test('sends SIGKILL after SIGTERM timeout and clears state when process exits', async () => {
    const started = await startHealthy();
    killKills.add(started.pid);

    const result = await env.stopXvfb();

    expect(result).toEqual({ running: false });
    expect(signals).toEqual(['SIGTERM', 'SIGKILL']);
    expect(env.getXvfbState()).toBeNull();
  }, 30000);

  test('returns XVFB_STOP_TIMEOUT when process survives SIGKILL', async () => {
    await startHealthy();

    const result = await env.stopXvfb();

    expect(result.error).toBe('XVFB_STOP_TIMEOUT');
    expect(signals).toEqual(['SIGTERM', 'SIGKILL']);
  }, 30000);
});

describe('restartXvfb()', () => {
  test('stops then starts', async () => {
    healthQueue.push(1, 0, 1, 0);

    const first = await env.startXvfb();
    termKills.add(first.pid);

    const result = await env.restartXvfb();

    expect(result.running).toBe(true);
    expect(result.pid).not.toBe(first.pid);
    expect(xvfbCount).toBe(2);
    expect(env.getXvfbState().pid).toBe(result.pid);
  });
});

describe('assertXvfbRunning()', () => {
  test('throws XVFB_NOT_RUNNING when not running', () => {
    let caught;

    try {
      env.assertXvfbRunning();
    } catch (err) {
      caught = err;
    }

    expect(caught).toBeDefined();
    expect(caught.code).toBe('XVFB_NOT_RUNNING');
    expect(caught.result).toEqual({
      error: 'XVFB_NOT_RUNNING',
      detail: 'Xvfb display :99 is not running',
    });
  });

  test('returns state when running', async () => {
    const started = await startHealthy();

    const state = env.assertXvfbRunning();

    expect(state.running).toBe(true);
    expect(state.pid).toBe(started.pid);
  });
});

describe('getXvfbState()', () => {
  test('returns null when not running', () => {
    expect(env.getXvfbState()).toBeNull();
  });

  test('returns state when running', async () => {
    const started = await startHealthy();

    const state = env.getXvfbState();

    expect(state.running).toBe(true);
    expect(state.display).toBe(':99');
    expect(state.pid).toBe(started.pid);
    expect(state.width).toBe(960);
    expect(state.height).toBe(960);
    expect(state.depth).toBe(24);
    expect(state.display_env).toBe(':99');
  });
});

describe('locked constants', () => {
  test('exports correct values', () => {
    expect(env._constants.XVFB_DISPLAY).toBe(':99');
    expect(env._constants.XVFB_WIDTH).toBe(960);
    expect(env._constants.XVFB_HEIGHT).toBe(960);
    expect(env._constants.XVFB_DEPTH).toBe(24);
    expect(env._constants.XVFB_START_TIMEOUT_MS).toBe(5000);
    expect(env._constants.XVFB_STOP_TIMEOUT_MS).toBe(5000);
    expect(env._constants.XVFB_POLL_MS).toBe(100);
  });
});
