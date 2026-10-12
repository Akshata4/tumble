const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

async function asJson(response) {
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      // body wasn't JSON -- fall back to statusText
    }
    throw new Error(detail);
  }
  return response.json();
}

export async function createSession() {
  const res = await fetch(`${API_BASE}/sessions`, { method: "POST" });
  return asJson(res);
}

export async function uploadDataset(sessionId, file, target) {
  const body = new FormData();
  body.append("target", target);
  body.append("file", file);
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/dataset`, { method: "POST", body });
  return asJson(res);
}

export async function startSession(sessionId, { metric, minimize, prompt, turnstileToken }) {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ metric, minimize, prompt: prompt || null, turnstile_token: turnstileToken || null }),
  });
  return asJson(res);
}

export async function getStatus(sessionId) {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/status`);
  return asJson(res);
}

// Full history from the Trajectory's own file, not the live SSE queue --
// safe to call any number of times. Used to repopulate the feed when
// resuming a session (e.g. a reopened session link) instead of streaming.
export async function getEvents(sessionId) {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/events`);
  return asJson(res);
}

export async function getLimits() {
  const res = await fetch(`${API_BASE}/limits`);
  return asJson(res);
}

export async function getReportText(sessionId) {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/report`);
  if (!res.ok) return null;
  return res.text();
}

export function scriptDownloadUrl(sessionId) {
  return `${API_BASE}/sessions/${sessionId}/download/script`;
}

// Same endpoint as the download link -- the Content-Disposition header
// only affects direct browser navigation, not a fetch() read, so this is
// free to reuse for showing the code inline.
export async function getScriptText(sessionId) {
  const res = await fetch(scriptDownloadUrl(sessionId));
  if (!res.ok) return null;
  return res.text();
}

// One line per SSE message, JSON-encoded -- matches Trajectory's event
// shape exactly (see trajectory.py). `onDone` fires once, when the server
// sends its `event: done` sentinel and closes the stream.
export function streamEvents(sessionId, onEvent, onDone) {
  const source = new EventSource(`${API_BASE}/sessions/${sessionId}/stream`);
  source.onmessage = (e) => onEvent(JSON.parse(e.data));
  source.addEventListener("done", () => {
    source.close();
    onDone();
  });
  source.onerror = () => {
    // The browser's EventSource auto-reconnects on transient errors; if the
    // server process is actually gone this just retries harmlessly, so we
    // don't treat this as fatal -- only the explicit "done" event ends the run.
  };
  return source;
}
