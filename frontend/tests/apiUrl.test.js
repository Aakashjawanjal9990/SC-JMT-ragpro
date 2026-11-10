import test from 'node:test';
import assert from 'node:assert/strict';
import { validateProductionApiUrl } from '../src/lib/apiUrl.js';

test('accepts a valid HTTPS API URL ending in /api', () => {
  const url = validateProductionApiUrl('https://api.example.com/api');

  assert.equal(url.origin, 'https://api.example.com');
  assert.equal(url.pathname, '/api');
});

test('accepts a trailing slash after /api', () => {
  const url = validateProductionApiUrl('https://api.example.com/api/');

  assert.equal(url.pathname, '/api/');
});

test('rejects a missing API URL', () => {
  assert.throws(() => validateProductionApiUrl(''), /must be set/);
});

test('rejects an invalid URL', () => {
  assert.throws(
    () => validateProductionApiUrl('not-a-url'),
    /valid absolute URL/,
  );
});

test('rejects HTTP in production', () => {
  assert.throws(
    () => validateProductionApiUrl('http://api.example.com/api'),
    /must use HTTPS/,
  );
});

test('rejects embedded credentials', () => {
  assert.throws(
    () => validateProductionApiUrl('https://user:password@api.example.com/api'),
    /embedded credentials/,
  );
});

test('rejects URLs without the required /api suffix', () => {
  assert.throws(
    () => validateProductionApiUrl('https://api.example.com'),
    /end with \/api/,
  );
});
