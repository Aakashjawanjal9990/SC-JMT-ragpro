// Letters, with optional trailing dots (Dr., A.) and ' or - inside names.
const NAME = /^\p{L}+\.?(?:[ '’-]+\p{L}+\.?)*$/u;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function validateName(value) {
  const name = value.trim();
  if (!name) return 'Enter your full name.';
  if (!NAME.test(name)) {
    return 'Name can contain letters only. Remove numbers or symbols.';
  }
  if (name.replace(/[^\p{L}]/gu, '').length < 2) return 'Name is too short.';
  return '';
}

export function validateEmail(value) {
  return EMAIL.test(value.trim()) ? '' : 'Enter a valid email address.';
}

export function validatePassword(value) {
  if (value.length < 8) return 'Use at least 8 characters.';
  if (!/[A-Za-z]/.test(value) || !/\d/.test(value)) {
    return 'Include at least one letter and one number.';
  }
  return '';
}

export const validateCode = (value) =>
  /^\d{6}$/.test(value) ? '' : 'Enter the 6-digit code.';

/** Validate a legal research question without restricting legal punctuation. */
export function validateQuery(value) {
  if (typeof value !== 'string' || !value.trim()) {
    return 'Enter a research question.';
  }
  const question = value.normalize('NFC').trim();
  if (question.length > 2000) {
    return 'Keep your research question under 2000 characters.';
  }
  // This range intentionally identifies non-printing control characters.
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/u.test(question)) {
    return 'Remove control characters from your question.';
  }
  if (
    /<\/?\s*[a-z][a-z0-9]*(?:\s|\/?>|$)|<!\s*(?:doctype|\[CDATA\b)/iu.test(
      question,
    )
  ) {
    return 'HTML or markup is not allowed in a question.';
  }
  return '';
}

/** "tanvi@gmail.com" -> "t•••@gmail.com" */
export function maskEmail(email) {
  const [local, domain] = email.split('@');
  return `${local[0]}${'•'.repeat(Math.max(local.length - 1, 2))}@${domain}`;
}
