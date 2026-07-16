// ProcessForge UI — shared client-side helpers.
//
// Auth state lives entirely in localStorage: `pf_token` (the bearer token
// returned by POST /auth/login) and `pf_username` (for display only, not
// used for anything security-relevant). Every authenticated fetch should go
// through fetchWithAuth() below so the Authorization header is never
// forgotten on a new page.

function getToken() {
  return localStorage.getItem("pf_token");
}

function getUsername() {
  return localStorage.getItem("pf_username");
}

function isLoggedIn() {
  return !!getToken();
}

// Wraps fetch() to add `Authorization: Bearer <token>` to the request,
// merging with any headers the caller already supplied. Returns the raw
// fetch() promise — callers are responsible for checking response.ok /
// response.status and calling .json() themselves.
async function fetchWithAuth(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = getToken();
  if (token) {
    headers.set("Authorization", "Bearer " + token);
  }
  return fetch(url, { ...options, headers });
}

// Logs out client-side no matter what the server says: an expired or
// already-invalid token must never trap the user in a logged-in-looking
// state. The server call is best-effort only.
async function logout() {
  try {
    await fetchWithAuth("/auth/logout", { method: "POST" });
  } catch (err) {
    // Network error or similar — still proceed to clear local state below.
  }
  localStorage.removeItem("pf_token");
  localStorage.removeItem("pf_username");
  window.location.href = "/ui/login";
}

// Reusable guard for pages that require a logged-in user. Not wired into
// anything yet (only /ui/login exists so far) — future authenticated pages
// should call this on load.
function requireAuth() {
  if (!isLoggedIn()) {
    window.location.href = "/ui/login";
  }
}

// Wire up the shared nav's "Log Out" control, if present on this page.
document.addEventListener("DOMContentLoaded", () => {
  const logoutButton = document.getElementById("nav-logout");
  if (logoutButton) {
    logoutButton.addEventListener("click", (event) => {
      event.preventDefault();
      logout();
    });
  }
});
