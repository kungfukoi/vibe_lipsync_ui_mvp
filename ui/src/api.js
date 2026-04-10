/** localStorage keys for BYOK (sent as headers on every API call). */
export const LS_ELEVEN = "lipsync_byok_eleven";
export const LS_FAL = "lipsync_byok_fal";

export function getByokHeaders() {
  try {
    const e = localStorage.getItem(LS_ELEVEN)?.trim();
    const f = localStorage.getItem(LS_FAL)?.trim();
    const h = {};
    if (e) h["X-Eleven-Api-Key"] = e;
    if (f) h["X-Fal-Key"] = f;
    return h;
  } catch {
    return {};
  }
}

/** fetch() with BYOK headers merged in (does not set Content-Type; safe for FormData). */
export function apiFetch(url, init = {}) {
  const merged = { ...init, headers: { ...getByokHeaders(), ...(init.headers || {}) } };
  return fetch(url, merged);
}
