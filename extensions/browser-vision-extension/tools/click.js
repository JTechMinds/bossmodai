const { setPrecision, validateCoordinate, cellCenter, gridMetadata } = require('../grid/protocol');

module.exports = {
  name: 'click',
  description: 'Click a grid cell coordinate on the current page',
  params: {
    col: { type: 'integer', required: true },
    row: { type: 'integer', required: true },
    precision: { type: 'integer', required: true, description: 'Grid spacing in px (cell size). Must be a positive integer 1-960.' },
  },
  async handler(params, getCapture) {
    const N = params.precision;

    // Step 1: Validate precision
    const prec = setPrecision(N);
    if (!prec.valid) {
      return { error: 'INVALID_PRECISION', detail: 'precision must be a positive integer 1-960; got ' + N };
    }

    // Step 2: Validate coordinate bounds
    const vc = validateCoordinate(N, params.col, params.row);
    if (!vc.valid) {
      return { error: vc.error, detail: 'col ' + params.col + ' exceeds ' + prec.cols + ' cols for precision ' + N };
    }

    // Step 3: Pre-click re-screenshot (mandatory, non-optional)
    const cap = getCapture();
    if (!cap || !cap.hasPage) {
      return { error: 'NO_PAGE', detail: 'Call view first' };
    }

    let preShot;
    try {
      preShot = await cap.screenshot('preclick');
    } catch (e) {
      return { error: 'PRECLICK_CAPTURE_FAILED', detail: e.message };
    }
    cap.logAction({
      tool: 'click',
      step: 'pre_click_screenshot',
      screenshot: preShot,
      grid: { precision: N, cols: prec.cols, rows: prec.rows },
    });

    // Step 4: Compute pixel center
    const center = cellCenter(N, params.col, params.row);

    // Step 5: Execute click
    await cap.click(center.x, center.y);
    cap.logAction({ tool: 'click', step: 'execute', at: { x: center.x, y: center.y } });

    // Step 6: Wait 300 ms for page to settle
    await cap.waitForMs(300);

    // Step 7: Capture post-click screenshot
    const postShot = await cap.screenshot('postclick');
    cap.logAction({ tool: 'click', step: 'post_click_screenshot', screenshot: postShot });

    // Step 8: Return structured result
    return {
      clicked_at: center,
      pre_click_screenshot_path: preShot,
      post_click_screenshot_path: postShot,
      grid: gridMetadata(N),
      timestamp: new Date().toISOString(),
    };
  },
};
