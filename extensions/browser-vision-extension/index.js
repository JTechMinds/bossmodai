const { HeadlessCapture } = require('./capture/headless');
const xvfb = require('./xvfb/env');
const view = require('./tools/view');
const click = require('./tools/click');
const type = require('./tools/type');
const scroll = require('./tools/scroll');

let capture = null;

function getHandlers() {
  return {
    view: (params) => view.handler(params, () => capture),
    click: (params) => click.handler(params, () => capture),
    type: (params) => type.handler(params, () => capture),
    scroll: (params) => scroll.handler(params, () => capture),
  };
}

async function register(host) {
  const tools = [view, click, type, scroll];
  for (const tool of tools) {
    host.registerTool(tool.name, tool.description, tool.params, (params) => tool.handler(params, () => capture));
  }
}

async function init() {
  const xvfbResult = await xvfb.startXvfb();
  if (xvfbResult.error) {
    throw Object.assign(new Error(xvfbResult.detail), { code: xvfbResult.error, result: xvfbResult });
  }
  capture = new HeadlessCapture();
  await capture.init();
}

async function destroy() {
  if (capture) {
    await capture.close();
    capture = null;
  }
  const stopResult = await xvfb.stopXvfb();
  if (stopResult.error) {
    throw Object.assign(new Error(stopResult.detail), { code: stopResult.error, result: stopResult });
  }
}

module.exports = { register, init, destroy, getHandlers };
