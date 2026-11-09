// Run with: node --test tests/js   (needs `npm install` inside tests/js)
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const script = fs.readFileSync(path.join(__dirname, '../../app/static/js/nexus.js'), 'utf8');

const PAGE = `<!doctype html><html><head><meta name="csrf-token" content="tok123"></head><body>
<div id="chat" data-meeting-id="7"><div class="bot">Hello</div></div>
<input id="q"><button id="send" type="button">↑</button>
<form id="meeting-form" data-max-audio-mb="1"><div class="drop"><input type="file" name="audio"><b>Choose or drop audio</b></div>
<button type="submit">Run AI analysis</button></form>
<form id="status-form"><select data-autosubmit name="status"><option>Pending</option><option>Completed</option></select></form>
<canvas id="chart"></canvas></body></html>`;

function setup(fetchImpl) {
  const dom = new JSDOM(PAGE, { runScripts: 'outside-only', url: 'http://localhost/meeting/7' });
  dom.window.fetch = fetchImpl;
  dom.window.HTMLFormElement.prototype.submit = function () { this.dataset.submitted = 'yes'; };
  dom.window.eval(script);
  dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
  return dom;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 20));
const json = (status, body) => Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

test('AI output is rendered as text, never as HTML', async () => {
  const evil = '<img src=x onerror="window.pwned=1"><script>window.pwned=1</script>';
  const dom = setup(() => json(200, { answer: evil }));
  const { document } = dom.window;
  document.getElementById('q').value = 'hi <b>there</b>';
  document.getElementById('send').click();
  await flush();
  const lines = [...document.querySelectorAll('#chat > div')];
  assert.strictEqual(lines.length, 3);
  assert.strictEqual(lines[1].className, 'user');
  assert.strictEqual(lines[1].textContent, 'hi <b>there</b>');
  assert.strictEqual(lines[2].textContent, evil);
  assert.strictEqual(document.querySelector('#chat img'), null);
  assert.strictEqual(document.querySelector('#chat script'), null);
  assert.strictEqual(dom.window.pwned, undefined);
});

test('request carries the CSRF header, meeting id and message', async () => {
  let seen;
  const dom = setup((url, options) => { seen = { url, options }; return json(200, { answer: 'ok' }); });
  dom.window.document.getElementById('q').value = 'Who owns it?';
  dom.window.document.getElementById('send').click();
  await flush();
  assert.strictEqual(seen.url, '/chat/7');
  assert.strictEqual(seen.options.headers['X-CSRFToken'], 'tok123');
  assert.strictEqual(JSON.parse(seen.options.body).message, 'Who owns it?');
});

test('server errors and network failures are shown and the input is re-enabled', async () => {
  let mode = 'server';
  const dom = setup(() => (mode === 'server' ? json(503, { error: 'The AI is down.' }) : Promise.reject(new Error('offline'))));
  const { document } = dom.window;
  const ask = async () => { document.getElementById('q').value = 'q'; document.getElementById('send').click(); await flush(); };
  await ask();
  assert.ok([...document.querySelectorAll('#chat .bot')].some((n) => n.textContent === 'The AI is down.'));
  mode = 'network';
  await ask();
  assert.ok([...document.querySelectorAll('#chat .bot')].some((n) => /unavailable/.test(n.textContent)));
  assert.strictEqual(document.getElementById('q').disabled, false);
  assert.strictEqual(document.getElementById('send').disabled, false);
  assert.strictEqual(document.getElementById('think'), null);
});

test('non-JSON error responses do not break the chat', async () => {
  const dom = setup(() => Promise.resolve({ ok: false, status: 500, json: () => Promise.reject(new Error('html')) }));
  dom.window.document.getElementById('q').value = 'q';
  dom.window.document.getElementById('send').click();
  await flush();
  assert.match(dom.window.document.querySelector('#chat').lastElementChild.textContent, /could not answer/);
});

test('empty questions are ignored and Enter sends', async () => {
  let calls = 0;
  const dom = setup(() => { calls += 1; return json(200, { answer: 'ok' }); });
  const { document } = dom.window;
  document.getElementById('q').value = '   ';
  document.getElementById('send').click();
  await flush();
  assert.strictEqual(calls, 0);
  document.getElementById('q').value = 'hello';
  document.getElementById('q').dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await flush();
  assert.strictEqual(calls, 1);
});

test('meeting form disables the button on submit', () => {
  const dom = setup(() => json(200, {}));
  const { document } = dom.window;
  const form = document.getElementById('meeting-form');
  form.dispatchEvent(new dom.window.Event('submit', { cancelable: true }));
  const button = form.querySelector('button[type="submit"]');
  assert.strictEqual(button.disabled, true);
  assert.match(button.textContent, /Analyzing/);
});

test('status selects auto-submit their form', () => {
  const dom = setup(() => json(200, {}));
  const { document } = dom.window;
  document.querySelector('select').dispatchEvent(new dom.window.Event('change'));
  assert.strictEqual(document.getElementById('status-form').dataset.submitted, 'yes');
});

test('renderChart degrades gracefully when Chart.js is missing', () => {
  const dom = setup(() => json(200, {}));
  assert.strictEqual(dom.window.NEXUS.renderChart('chart', {}), null);
  assert.match(dom.window.document.body.textContent, /could not be loaded/);
});
