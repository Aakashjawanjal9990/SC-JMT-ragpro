import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import process from 'node:process';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

const originalApiUrl = process.env.VITE_API_URL;
process.env.VITE_API_URL = '/api';

const vite = await createServer({
  configFile: 'vite.config.js',
  server: { middlewareMode: true },
  appType: 'custom',
});
const { AssistantAnswer } = await vite.ssrLoadModule('/src/pages/Home.jsx');

after(async () => {
  await vite.close();
  if (originalApiUrl === undefined) {
    delete process.env.VITE_API_URL;
  } else {
    process.env.VITE_API_URL = originalApiUrl;
  }
});

const renderAnswer = (message) =>
  renderToStaticMarkup(createElement(AssistantAnswer, { message }));

test('renders normal assistant Markdown answers as before', () => {
  const html = renderAnswer({
    role: 'assistant',
    text: 'The **holding** remains *important*.',
    status: 'success',
    limitations: null,
  });

  assert.match(
    html,
    /The <strong>holding<\/strong> remains <em>important<\/em>\./u,
  );
  assert.doesNotMatch(html, /Limitations/u);
});

test('renders the original insufficient-evidence explanation and limitations', () => {
  const explanation = 'The available judgments do not address this issue.';
  const html = renderAnswer({
    role: 'assistant',
    text: explanation,
    status: 'insufficient_evidence',
    limitations: ['Only two judgments were available.'],
  });

  assert.ok(html.includes(explanation));
  assert.ok(html.includes('Limitations'));
  assert.ok(html.includes('Only two judgments were available.'));
});

test('shows limitations when an insufficient-evidence answer is empty', () => {
  const html = renderAnswer({
    role: 'assistant',
    text: '',
    status: 'insufficient_evidence',
    limitations: ['The search covered a limited set of judgments.'],
  });

  assert.ok(html.includes('The search covered a limited set of judgments.'));
  assert.ok(html.includes('Limitations'));
  assert.doesNotMatch(html, /I could not find enough support/u);
});
