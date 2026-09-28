const { HeadlessCapture } = require('../capture/headless');
const ext = require('../index');
const viewTool = require('../tools/view');
const clickTool = require('../tools/click');
const typeTool = require('../tools/type');
const scrollTool = require('../tools/scroll');

jest.mock('../capture/headless', () => {
  const mockInstance = {
    init: jest.fn().mockResolvedValue(undefined),
    close: jest.fn().mockResolvedValue(undefined),
  };

  const MockHeadlessCapture = jest.fn().mockImplementation(() => mockInstance);

  return {
    HeadlessCapture: MockHeadlessCapture,
  };
});

jest.mock('../xvfb/env', () => {
  const state = {
    running: true,
    display: ':99',
    pid: 12345,
    width: 960,
    height: 960,
    depth: 24,
    started_at: '2025-01-01T00:00:00.000Z',
    display_env: ':99',
  };

  return {
    startXvfb: jest.fn().mockResolvedValue(state),
    stopXvfb: jest.fn().mockResolvedValue({ running: false }),
    restartXvfb: jest.fn(),
    getXvfbState: jest.fn().mockReturnValue(state),
    assertXvfbRunning: jest.fn().mockReturnValue(state),
  };
});

describe('extension lifecycle', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  afterEach(async () => {
    await ext.destroy();
  });

  test('register() registers exactly the four manifest tools with the host', async () => {
    const registered = [];
    const host = {
      registerTool(name, description, params, handler) {
        registered.push({ name, description, params, handler });
      },
    };

    await ext.register(host);

    expect(registered).toHaveLength(4);
    expect(registered.map((tool) => tool.name).sort()).toEqual([
      'click',
      'scroll',
      'type',
      'view',
    ]);

    for (const tool of registered) {
      expect(typeof tool.description).toBe('string');
      expect(tool.description.length).toBeGreaterThan(0);
      expect(tool.params).toEqual(expect.objectContaining({}));
      expect(typeof tool.handler).toBe('function');
    }
  });

  test('tool handlers called before registration or initialization return NO_PAGE', async () => {
    const nullCapture = () => null;

    await expect(
      viewTool.handler({ url: 'https://example.com' }, nullCapture)
    ).resolves.toEqual({
      error: 'NO_PAGE',
      detail: 'Call view first',
    });

    await expect(
      clickTool.handler({ col: 0, row: 0, precision: 48 }, nullCapture)
    ).resolves.toEqual({
      error: 'NO_PAGE',
      detail: 'Call view first',
    });

    await expect(
      typeTool.handler(
        { col: 0, row: 0, precision: 48, text: 'hello' },
        nullCapture
      )
    ).resolves.toEqual({
      error: 'NO_PAGE',
      detail: 'Call view first',
    });

    await expect(
      scrollTool.handler(
        { col: 0, row: 0, precision: 48, direction: 'down' },
        nullCapture
      )
    ).resolves.toEqual({
      error: 'NO_PAGE',
      detail: 'Call view first',
    });
  });

  test('init() creates a HeadlessCapture instance and calls init on it', async () => {
    await ext.init();

    expect(HeadlessCapture).toHaveBeenCalledTimes(1);
    const instance = HeadlessCapture.mock.results[0].value;
    expect(instance.init).toHaveBeenCalledTimes(1);
  });

  test('destroy() closes the capture instance and is idempotent', async () => {
    await ext.init();
    const instance = HeadlessCapture.mock.results[0].value;

    await ext.destroy();
    expect(instance.close).toHaveBeenCalledTimes(1);

    await expect(ext.destroy()).resolves.not.toThrow();
    expect(instance.close).toHaveBeenCalledTimes(1);
  });
});

const fs = require('fs');
const path = require('path');
const { parseBvCommand, dispatchBvCommand } = require('../cli');

describe('extension card (§2.5)', () => {
  const cardPath = path.join(__dirname, '..', 'extension-card.json');
  const manifestPath = path.join(__dirname, '..', 'manifest.json');

  test('extension-card.json exists and parses as valid JSON', () => {
    expect(fs.existsSync(cardPath)).toBe(true);
    const raw = fs.readFileSync(cardPath, 'utf8');
    const card = JSON.parse(raw);
    expect(card).toBeDefined();
  });

  test('extension card name and version match manifest.json', () => {
    const card = JSON.parse(fs.readFileSync(cardPath, 'utf8'));
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    expect(card.name).toBe(manifest.name);
    expect(card.version).toBe(manifest.version);
  });

  test('extension card lists exactly view, click, type, scroll in manifest order with non-empty one_liner', () => {
    const card = JSON.parse(fs.readFileSync(cardPath, 'utf8'));
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));

    // manifest.tools is a string array
    expect(card.tools.map((tool) => tool.name)).toEqual(manifest.tools);
    expect(card.tools.map((tool) => tool.name)).toEqual([
      'view',
      'click',
      'type',
      'scroll',
    ]);

    for (const tool of card.tools) {
      expect(typeof tool.one_liner).toBe('string');
      expect(tool.one_liner.length).toBeGreaterThan(0);
    }
  });
});

describe('CLI invocation pattern (§2.6)', () => {
  describe('parseBvCommand', () => {
    test('valid view command', () => {
      const parsed = parseBvCommand([
        'view',
        '--params-json',
        JSON.stringify({ url: 'https://example.com' }),
      ]);

      expect(parsed.tool).toBe('view');
      expect(parsed.paramsJson).toBe(JSON.stringify({ url: 'https://example.com' }));
    });

    test('unknown tool returns UNKNOWN_TOOL', () => {
      const parsed = parseBvCommand(['bogus', '--params-json', '{}']);

      expect(parsed.error).toBe('UNKNOWN_TOOL');
    });

    test('missing --params-json returns MISSING_PARAMS_JSON', () => {
      const parsed = parseBvCommand(['view']);

      expect(parsed.error).toBe('MISSING_PARAMS_JSON');
    });

    test('invalid JSON returns INVALID_PARAMS_JSON', () => {
      const parsed = parseBvCommand(['view', '--params-json', '{not-json']);

      expect(parsed.error).toBe('INVALID_PARAMS_JSON');
    });
  });

  describe('dispatchBvCommand', () => {
    test('dispatches to the correct handler and returns its result', async () => {
      const handlers = {
        view: jest.fn().mockResolvedValue({ ok: true }),
      };

      const result = await dispatchBvCommand(
        handlers,
        'view',
        JSON.stringify({ url: 'https://example.com' })
      );

      expect(handlers.view).toHaveBeenCalledWith({ url: 'https://example.com' });
      expect(result).toEqual({ ok: true });
    });

    test('returns INVALID_PARAMS_JSON without calling handler on bad JSON', async () => {
      const handlers = {
        view: jest.fn().mockResolvedValue({ ok: true }),
      };

      const result = await dispatchBvCommand(handlers, 'view', '{not-json');

      expect(handlers.view).not.toHaveBeenCalled();
      expect(result.error).toBe('INVALID_PARAMS_JSON');
    });

    test('returns UNKNOWN_TOOL for unregistered tool', async () => {
      const handlers = {};

      const result = await dispatchBvCommand(
        handlers,
        'view',
        JSON.stringify({ url: 'https://example.com' })
      );

      expect(result.error).toBe('UNKNOWN_TOOL');
    });

    test('returns handler error object unchanged', async () => {
      const error = { error: 'NO_PAGE', detail: 'Call view first' };
      const handlers = {
        view: jest.fn().mockResolvedValue(error),
      };

      const result = await dispatchBvCommand(
        handlers,
        'view',
        JSON.stringify({ url: 'https://example.com' })
      );

      expect(result).toEqual(error);
    });
  });
});
