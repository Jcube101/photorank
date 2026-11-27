// Wording for photos that couldn't be prepared for upload, so were not sent:
// compression failed and they are not a JPEG whose metadata can be stripped
// (compress.js), e.g. a HEIC this browser can't decode.

/** "a", "a and b", "a, b and c". */
export function listNames(names) {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** The error when fewer than `min` photos are left to rank. */
export function tooFewMessage(names, left, min) {
  const rest = left === 0 ? "no photos are" : left === 1 ? "only 1 photo is" : `only ${left} photos are`;
  return `Couldn't prepare ${listNames(names)} for upload, so ${rest} left to rank. PhotoRank needs at least ${min}.`;
}
