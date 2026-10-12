import { useEffect, useRef } from "react";

function truncate(str, n = 360) {
  return str.length > n ? str.slice(0, n) + " …" : str;
}

// write_file's `content` can be a whole script or report -- dumping it as
// escaped JSON is unreadable, so summarize it instead of the other tool
// calls' args, which are already short.
function argsPreview(name, args) {
  if (name === "write_file" && typeof args?.content === "string") {
    return `path="${args.path}", ${args.content.length} chars`;
  }
  return truncate(JSON.stringify(args ?? {}));
}

// One <Line> per Trajectory event. See trajectory.py for the event shapes
// this switches on -- the frontend never invents its own vocabulary here,
// it just renders exactly what the harness logged.
function Line({ event }) {
  switch (event.type) {
    case "meta":
      return <div className="feed-line log">using {event.model} via {event.provider}</div>;

    case "assistant": {
      const calls = event.tool_calls || [];
      if (calls.length > 0) {
        return (
          <>
            {calls.map((c) => (
              <div className="feed-line call" key={c.id}>
                → {c.name}({argsPreview(c.name, c.args)})
              </div>
            ))}
          </>
        );
      }
      if (event.content) {
        return <div className="feed-line final">{event.content}</div>;
      }
      return null;
    }

    case "tool": {
      const kept = event.name === "run_experiment" && event.result.includes("NEW BEST");
      return (
        <div className={`feed-line result${kept ? " kept" : ""}`}>
          {event.name} → {truncate(event.result)}
        </div>
      );
    }

    case "notice":
      return <div className="feed-line notice">note: {event.content}</div>;

    case "log":
      return <div className="feed-line log">{event.content}</div>;

    default:
      return null;
  }
}

export default function TrajectoryFeed({ events }) {
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [events.length]);

  return (
    <div className="feed">
      {events.map((event, i) => (
        <Line event={event} key={i} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
