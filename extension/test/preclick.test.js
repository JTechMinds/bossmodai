const click = require('../tools/click');

describe('preclick', () => {
  let capture;

  beforeEach(() => {
    jest.clearAllMocks();
    capture = {
      hasPage: true,
      screenshot: jest.fn(),
      click: jest.fn(),
      logAction: jest.fn(),
      waitForMs: jest.fn().mockResolvedValue(undefined),
    };
  });

  test('returns PRECLICK_CAPTURE_FAILED and does not click when pre-click screenshot fails', async () => {
    capture.screenshot.mockRejectedValue(new Error('capture failed'));

    const result = await click.handler(
      { col: 0, row: 0, precision: 48 },
      () => capture
    );

    expect(result).toEqual(
      expect.objectContaining({ error: 'PRECLICK_CAPTURE_FAILED' })
    );
    expect(capture.click).not.toHaveBeenCalled();
  });

  test('logs pre_click_screenshot before execute for successful click', async () => {
    capture.screenshot
      .mockResolvedValueOnce('/tmp/bv-preclick-123.png')
      .mockResolvedValueOnce('/tmp/bv-postclick-456.png');
    capture.click.mockResolvedValue(undefined);

    const result = await click.handler(
      { col: 0, row: 0, precision: 48 },
      () => capture
    );

    expect(result).toEqual(
      expect.objectContaining({
        clicked_at: { x: 24, y: 24 },
        pre_click_screenshot_path: '/tmp/bv-preclick-123.png',
        post_click_screenshot_path: '/tmp/bv-postclick-456.png',
      })
    );

    const steps = capture.logAction.mock.calls.map((call) => call[0].step);
    expect(steps).toContain('pre_click_screenshot');
    expect(steps).toContain('execute');
    expect(steps.indexOf('pre_click_screenshot')).toBeLessThan(
      steps.indexOf('execute')
    );
  });
});
