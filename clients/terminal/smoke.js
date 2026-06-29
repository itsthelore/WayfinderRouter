// Headless smoke test: mount the app and assert it renders a frame, without a live gateway.
// ink-testing-library renders the initial frame synchronously, before the on-mount gateway probe
// resolves, so we can check the welcome screen mounts. Exits non-zero on failure.
import React from 'react';
import {render} from 'ink-testing-library';
import App from './dist/app.js';

const {lastFrame, unmount} = render(React.createElement(App, {baseUrl: 'http://127.0.0.1:8088'}));
const frame = lastFrame() || '';
unmount();

if (!frame.includes('no model call to decide')) {
  console.error('smoke: FAILED — welcome screen not found in first frame');
  console.error(frame.slice(0, 400));
  process.exit(1);
}
console.log('smoke: OK — app mounts and renders the welcome screen');
