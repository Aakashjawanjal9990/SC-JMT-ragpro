const HONORIFICS = new Set([
  'mr',
  'mrs',
  'ms',
  'miss',
  'dr',
  'adv',
  'advocate',
  'shri',
  'smt',
  'prof',
]);

const letterCount = (word) => word.replace(/[^\p{L}]/gu, '').length;

/** "tanvi jivatode" -> "Tanvi". Skips honorifics and single initials. */
export function getFirstName(fullName = '') {
  const words = fullName.trim().split(/\s+/).filter(Boolean);
  const first =
    words.find(
      (w) =>
        !HONORIFICS.has(w.replace(/\./g, '').toLowerCase()) &&
        letterCount(w) >= 2,
    ) || words[0];
  if (!first) return 'there';
  const clean = first.replace(/[^\p{L}'’-]/gu, '');
  return clean.charAt(0).toUpperCase() + clean.slice(1).toLowerCase();
}

/** Best available display name: full name, else the part of the email before @. */
export function displayName(user) {
  if (user?.name?.trim()) return user.name.trim();
  const local = user?.email?.split('@')[0] || '';
  return local.replace(/[._\d]+/g, ' ').trim() || 'User';
}
