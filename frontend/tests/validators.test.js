import test from 'node:test';
import assert from 'node:assert/strict';
import {
  MAX_PASSWORD_LENGTH,
  validatePassword,
  validatePasswordLength,
} from '../src/lib/validators.js';

test('password maximum is 128 characters', () => {
  assert.equal(MAX_PASSWORD_LENGTH, 128);
});

test('accepts a valid password at the 128-character limit', () => {
  const password = `A1${'x'.repeat(126)}`;
  assert.equal(password.length, 128);
  assert.equal(validatePassword(password), '');
});

test('rejects a password longer than 128 characters', () => {
  const password = `A1${'x'.repeat(127)}`;
  assert.equal(password.length, 129);
  assert.match(validatePassword(password), /128 characters or fewer/);
  assert.match(validatePasswordLength(password), /128 characters or fewer/);
});

test('keeps the minimum-length and composition requirements', () => {
  assert.match(validatePassword('Abc123'), /at least 8 characters/);
  assert.match(validatePassword('abcdefgh'), /letter and one number/);
  assert.equal(validatePassword('Abcdef12'), '');
});
