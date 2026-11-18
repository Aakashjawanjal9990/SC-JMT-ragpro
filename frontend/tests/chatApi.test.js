import assert from 'node:assert/strict';
import test from 'node:test';
import {
  api,
  createIdempotencyKey,
  normalizeQueryResponse,
} from '../src/lib/api.js';

test('normalizes normal answers without discarding response data or citations', () => {
  const normalized = normalizeQueryResponse({
    conversationId: 'conversation-1',
    answer: '  Answer text.  ',
    status: 'success',
    limitations: null,
    citations: [
      {
        case_name: 'Example v State',
        decision_date: '2020-01-01',
        original_page_numbers: [4, 5],
      },
    ],
    additionalField: 'preserved',
  });

  assert.equal(normalized.answer, 'Answer text.');
  assert.equal(normalized.status, 'success');
  assert.equal(normalized.limitations, null);
  assert.equal(normalized.additionalField, 'preserved');
  assert.deepEqual(normalized.sources, [
    { case: 'Example v State', year: '2020', page: '4-5' },
  ]);
  assert.equal(normalized.citations.length, 1);
});

test('preserves insufficient-evidence explanations and limitations', () => {
  const normalized = normalizeQueryResponse({
    status: 'insufficient_evidence',
    answer: 'The available judgments do not address this issue.',
    limitations: ['Only two judgments were available.'],
  });

  assert.equal(normalized.status, 'insufficient_evidence');
  assert.equal(
    normalized.answer,
    'The available judgments do not address this issue.',
  );
  assert.deepEqual(normalized.limitations, [
    'Only two judgments were available.',
  ]);
  assert.notEqual(
    normalized.answer,
    'I could not find enough support in the judgments to answer this. Try rephrasing or adding detail.',
  );
});

test('handles missing and null optional response fields safely', () => {
  const missing = normalizeQueryResponse(null);
  assert.equal(
    missing.answer,
    'I could not find enough support in the judgments to answer this. Try rephrasing or adding detail.',
  );
  assert.deepEqual(missing.sources, []);

  const nullFields = normalizeQueryResponse({
    status: null,
    answer: null,
    sources: null,
    citations: null,
    limitations: null,
  });
  assert.equal(nullFields.status, null);
  assert.equal(nullFields.limitations, null);
  assert.deepEqual(nullFields.sources, []);
});

test('creates distinct UUID idempotency keys for new chat messages', () => {
  const firstKey = createIdempotencyKey();
  const secondKey = createIdempotencyKey();

  assert.match(
    firstKey,
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu,
  );
  assert.notEqual(firstKey, secondKey);
});

test('sends a reused retry key and a different key for a separate message', async () => {
  const originalFetch = globalThis.fetch;
  const queryRequests = [];

  globalThis.fetch = async (url, options) => {
    if (url === '/auth/me') {
      return new Response(
        JSON.stringify({ csrfToken: 'csrf-test', user: {} }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      );
    }
    if (url === '/chat/interpret') {
      return new Response('{}', {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url === '/chat/query') {
      queryRequests.push(options);
      return new Response(JSON.stringify({ answer: 'Answer.' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url === '/auth/logout') {
      return new Response('{}', {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    throw new Error(`Unexpected request: ${url}`);
  };

  try {
    await api.me();
    const originalMessageKey = createIdempotencyKey();
    const separateMessageKey = createIdempotencyKey();

    await api.query('First message', null, false, originalMessageKey);
    await api.query('First message', null, false, originalMessageKey);
    await api.query('Second message', null, false, separateMessageKey);

    assert.deepEqual(
      queryRequests.map((options) => options.headers['Idempotency-Key']),
      [originalMessageKey, originalMessageKey, separateMessageKey],
    );
    assert.equal(queryRequests[0].credentials, 'include');
    assert.equal(queryRequests[0].headers['X-CSRF-Token'], 'csrf-test');
    await api.logout();
  } finally {
    globalThis.fetch = originalFetch;
  }
});
