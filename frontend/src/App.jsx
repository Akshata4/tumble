import { useEffect, useRef, useState } from "react";
import CodeViewer from "./components/CodeViewer";
import MarkdownReport from "./components/MarkdownReport";
import MetricChart from "./components/MetricChart";
import SetupForm from "./components/SetupForm";
import TrajectoryFeed from "./components/TrajectoryFeed";
import { getEvents, getLimits, getReportText, getScriptText, getStatus, scriptDownloadUrl, streamEvents } from "./api";

const SESSION_PATH = /^\/session\/([a-zA-Z0-9-]+)$/;
const TERMINAL_STATUSES = ["completed", "failed", "max_turns_exceeded", "time_limit_exceeded", "budget_exhausted"];

const RUN_RESULT = /^Iteration (\d+): (\w+)=([-\d.eE]+)\..*Current best: ([-\d.eE]+)\.$/;

// Every run_experiment result, in order -- the chart's whole series. Kept
// separate from `latestMetric` below (which only needs the newest one)
// since they read the same events for two different jobs.
function metricSeries(events) {
  const points = [];
  for (const e of events) {
    if (e.type !== "tool" || e.name !== "run_experiment") continue;
    const m = e.result.match(RUN_RESULT);
    if (m) points.push({ iteration: Number(m[1]), value: Number(m[3]), kept: e.result.includes("NEW BEST") });
  }
  return points;
}

function latestMetric(events) {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.type !== "tool" || e.name !== "run_experiment") continue;
    const m = e.result.match(RUN_RESULT);
    if (m) return { iteration: Number(m[1]), metricName: m[2], best: m[4] };
  }
  return null;
}

function StatusHero({ events, status }) {
  const metric = latestMetric(events);
  const done = status !== null;
  return (
    <div className="panel hero">
      <div className="hero-stat">
        <div className="value">{metric ? metric.best : "—"}</div>
        <div className="label">best {metric ? metric.metricName : "metric"}</div>
      </div>
      <div className="hero-stat">
        <div className="value">{metric ? metric.iteration + 1 : 0}</div>
        <div className="label">attempts so far</div>
      </div>
      <div className={`hero-status${done ? " done" : ""}`}>
        <span className="dot" />
        {done ? status.status : "running"}
      </div>
    </div>
  );
}

function Results({ sessionId, status, report, script, onStartNew }) {
  if (status.status !== "completed") {
    return (
      <div className="panel">
        <p className="error-text">
          Run ended without completing ({status.status}{status.error ? `: ${status.error}` : ""}).
        </p>
        <div className="actions">
          <button type="button" className="secondary" onClick={onStartNew}>
            Start a new run
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="panel">
      <div className="actions">
        <a className="button secondary" href={scriptDownloadUrl(sessionId)}>
          Download train.py
        </a>
        {script && <CodeViewer code={script} />}
        <button type="button" className="secondary" onClick={onStartNew}>
          Start a new run
        </button>
      </div>
      {report && <MarkdownReport content={report} />}
    </div>
  );
}

export default function App() {
  const [sessionId, setSessionId] = useState(null);
  const [events, setEvents] = useState([]);
  const [status, setStatus] = useState(null);
  const [report, setReport] = useState(null);
  const [script, setScript] = useState(null);
  const [limits, setLimits] = useState(null);
  const sourceRef = useRef(null);

  function handleStarted(id) {
    window.history.pushState({}, "", `/session/${id}`);
    setSessionId(id);
    setEvents([]);
    setStatus(null);
    setReport(null);
    setScript(null);
  }

  function startNew() {
    window.history.pushState({}, "", "/");
    setSessionId(null);
    setEvents([]);
    setStatus(null);
    setReport(null);
    setScript(null);
    getLimits().then(setLimits).catch(() => {});
  }

  // Resume a shared/reopened /session/<id> link. A still-running session
  // just attaches to /stream like any freshly-started one (below); a
  // finished one reads its full history from disk instead, since /stream
  // is a single-consumer queue that a late second viewer can't rely on.
  async function resumeSession(id) {
    try {
      const currentStatus = await getStatus(id);
      if (TERMINAL_STATUSES.includes(currentStatus.status)) {
        const isCompleted = currentStatus.status === "completed";
        const [pastEvents, reportText, scriptText] = await Promise.all([
          getEvents(id),
          isCompleted ? getReportText(id) : Promise.resolve(null),
          isCompleted ? getScriptText(id) : Promise.resolve(null),
        ]);
        setSessionId(id);
        setEvents(pastEvents);
        setStatus(currentStatus);
        setReport(reportText);
        setScript(scriptText);
      } else if (currentStatus.status === "running") {
        setSessionId(id);
        setEvents([]);
        setStatus(null);
      } else {
        // dataset uploaded but never started (or some other pre-run state)
        // -- nothing meaningful to resume into.
        window.history.replaceState({}, "", "/");
      }
    } catch {
      window.history.replaceState({}, "", "/"); // e.g. 404: server restarted since the link was made
    }
  }

  useEffect(() => {
    getLimits().then(setLimits).catch(() => {});

    const match = window.location.pathname.match(SESSION_PATH);
    if (match) resumeSession(match[1]);
  }, []);

  useEffect(() => {
    if (!sessionId || status !== null) return; // nothing live to stream (fresh run, or a resumed finished one)

    sourceRef.current = streamEvents(
      sessionId,
      (event) => setEvents((prev) => [...prev, event]),
      async () => {
        const finalStatus = await getStatus(sessionId);
        setStatus(finalStatus);
        if (finalStatus.status === "completed") {
          const [reportText, scriptText] = await Promise.all([getReportText(sessionId), getScriptText(sessionId)]);
          setReport(reportText);
          setScript(scriptText);
        }
        getLimits().then(setLimits).catch(() => {});
      },
    );

    return () => sourceRef.current?.close();
  }, [sessionId, status]);

  return (
    <>
      <div className="header">
        <p className="wordmark">Tumble</p>
        <p className="tagline">
          Point it at your data, tell it what to optimize, and it iteratively proposes changes, runs real
          experiments, and keeps only what actually improves the score.
        </p>
        {limits && !sessionId && (
          <p className="limits-note">
            {limits.runs_remaining_today} of {limits.max_runs_per_day} runs left today from your connection.
          </p>
        )}
      </div>

      {!sessionId && <SetupForm onStarted={handleStarted} />}

      {sessionId && (
        <>
          <StatusHero events={events} status={status} />
          {metricSeries(events).length > 0 && (
            <div className="panel">
              <label>Metric per attempt</label>
              <MetricChart series={metricSeries(events)} metricName={latestMetric(events)?.metricName ?? "metric"} />
            </div>
          )}
          <div className="panel">
            <TrajectoryFeed events={events} />
          </div>
          {status && <Results sessionId={sessionId} status={status} report={report} script={script} onStartNew={startNew} />}
        </>
      )}
    </>
  );
}
