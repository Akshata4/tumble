import { useEffect, useRef } from "react";

const SITE_KEY = import.meta.env.VITE_TURNSTILE_SITE_KEY;

export const isConfigured = Boolean(SITE_KEY);

// Renders nothing (and never blocks the caller) when no site key is
// configured -- local dev and the CLI never need this, same as the
// backend's TURNSTILE_SECRET_KEY being optional.
export default function Turnstile({ onToken }) {
  const containerRef = useRef(null);
  const widgetIdRef = useRef(null);

  useEffect(() => {
    if (!SITE_KEY) return;

    // api.js loads async -- window.turnstile may not exist on first mount.
    let cancelled = false;
    function render() {
      if (cancelled) return;
      if (!window.turnstile) {
        setTimeout(render, 100);
        return;
      }
      widgetIdRef.current = window.turnstile.render(containerRef.current, {
        sitekey: SITE_KEY,
        callback: onToken,
        "expired-callback": () => onToken(null),
        "error-callback": () => onToken(null),
      });
    }
    render();

    return () => {
      cancelled = true;
      if (widgetIdRef.current !== null) window.turnstile?.remove(widgetIdRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!SITE_KEY) return null;
  return <div ref={containerRef} className="field" />;
}
