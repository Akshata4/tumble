import { useState } from "react";
import { createSession, uploadDataset, startSession } from "../api";
import { readCsvPreview } from "../csv";
import DatasetTable from "./DatasetTable";
import Turnstile, { isConfigured as turnstileConfigured } from "./Turnstile";

export default function SetupForm({ onStarted }) {
  const [file, setFile] = useState(null);
  const [filePreview, setFilePreview] = useState(null);
  const [target, setTarget] = useState("");
  const [metric, setMetric] = useState("accuracy");
  const [minimize, setMinimize] = useState(false);
  const [prompt, setPrompt] = useState("");

  const [sessionId, setSessionId] = useState(null);
  const [preview, setPreview] = useState(null);
  const [phase, setPhase] = useState("idle"); // idle -> loading -> loaded -> starting
  const [error, setError] = useState(null);
  const [turnstileToken, setTurnstileToken] = useState(null);

  async function pickFile(e) {
    const picked = e.target.files[0];
    setFile(picked);
    setFilePreview(picked ? await readCsvPreview(picked) : null);
  }

  async function loadDataset(e) {
    e.preventDefault();
    setError(null);
    setPhase("loading");
    try {
      const { session_id } = await createSession();
      const { preview } = await uploadDataset(session_id, file, target);
      setSessionId(session_id);
      setPreview(preview);
      setPhase("loaded");
    } catch (err) {
      setError(err.message);
      setPhase("idle");
    }
  }

  async function start(e) {
    e.preventDefault();
    setError(null);
    setPhase("starting");
    try {
      await startSession(sessionId, { metric, minimize, prompt, turnstileToken });
      onStarted(sessionId);
    } catch (err) {
      setError(err.message);
      setPhase("loaded");
    }
  }

  const datasetReady = phase === "loaded";

  return (
    <div className="panel">
      <form onSubmit={datasetReady ? start : loadDataset}>
        <div className="row">
          <div className="field">
            <label htmlFor="file">Dataset (CSV)</label>
            <input
              id="file"
              type="file"
              accept=".csv"
              disabled={datasetReady}
              onChange={pickFile}
              required
            />
          </div>
          <div className="field">
            <label htmlFor="target">Target column</label>
            <input
              id="target"
              type="text"
              placeholder="e.g. diagnosis"
              value={target}
              disabled={datasetReady}
              onChange={(e) => setTarget(e.target.value)}
              required
            />
          </div>
        </div>

        {filePreview && !datasetReady && (
          <div className="field">
            <label>First rows of {file.name} — click a column to use it as the target</label>
            <DatasetTable data={filePreview} selected={target} onSelect={setTarget} />
          </div>
        )}

        {!datasetReady && (
          <button type="submit" disabled={phase === "loading" || !file || !target}>
            {phase === "loading" ? "Loading dataset…" : "Load dataset"}
          </button>
        )}

        {preview && (
          <div className="field" style={{ marginTop: 16 }}>
            <label>Summary</label>
            <div className="preview-block">{preview}</div>
          </div>
        )}

        {datasetReady && (
          <>
            <div className="row">
              <div className="field">
                <label htmlFor="metric">Metric to optimize</label>
                <input id="metric" type="text" value={metric} onChange={(e) => setMetric(e.target.value)} />
              </div>
              <div className="field checkbox-field" style={{ alignSelf: "flex-end", marginBottom: 16 }}>
                <input
                  id="minimize"
                  type="checkbox"
                  checked={minimize}
                  onChange={(e) => setMinimize(e.target.checked)}
                />
                <label htmlFor="minimize">Lower is better</label>
              </div>
            </div>
            <div className="field">
              <label htmlFor="prompt">Extra instructions (optional)</label>
              <textarea
                id="prompt"
                placeholder="Anything specific you want the agent to try or avoid"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
              />
            </div>
            {turnstileConfigured && <Turnstile onToken={setTurnstileToken} />}
            <button type="submit" disabled={phase === "starting" || (turnstileConfigured && !turnstileToken)}>
              {phase === "starting" ? "Starting…" : "Start run"}
            </button>
          </>
        )}

        {error && <p className="error-text">{error}</p>}
      </form>
    </div>
  );
}
