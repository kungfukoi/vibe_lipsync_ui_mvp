/** localStorage keys for BYOK (sent as headers on every API call). */
export const LS_ELEVEN = "lipsync_byok_eleven";
export const LS_FAL = "lipsync_byok_fal";

/** Enable console progress logs for every apiFetch (start + finish + duration). */
export const LS_DEBUG_API = "lipsync_debug_api";

/**
 * Console "progress" for API calls is off by default. Enable any one of:
 * - localStorage:  localStorage.setItem('lipsync_debug_api', '1')
 * - env (Vite):    VITE_DEBUG_API=true  in ui/.env.development
 * - DevTools:      window.__lipsyncDebugApi = true
 */
export function isApiDebugEnabled() {
  try {
    if (typeof window !== "undefined" && window.__lipsyncDebugApi) return true;
    if (import.meta.env.VITE_DEBUG_API === "true") return true;
    return localStorage.getItem(LS_DEBUG_API) === "1";
  } catch {
    return import.meta.env.VITE_DEBUG_API === "true";
  }
}

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
  const debug = isApiDebugEnabled();
  if (!debug) {
    return fetch(url, merged);
  }

  const method = (merged.method || "GET").toUpperCase();
  const t0 = performance.now();
  const shortUrl = typeof url === "string" ? url : String(url);
  console.log(`[lipsync] → ${method} ${shortUrl}`);

  return fetch(url, merged).then(
    (res) => {
      const sec = ((performance.now() - t0) / 1000).toFixed(2);
      console.log(`[lipsync] ← ${method} ${shortUrl}  ${res.status}  (${sec}s)`);
      return res;
    },
    (err) => {
      const sec = ((performance.now() - t0) / 1000).toFixed(2);
      console.warn(`[lipsync] ✗ ${method} ${shortUrl}  (${sec}s)`, err);
      throw err;
    },
  );
}
