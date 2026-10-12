import { useState } from "react";

export default function CodeViewer({ code }) {
  const [visible, setVisible] = useState(false);
  const [copied, setCopied] = useState(false);

  async function copy() {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <>
      <button type="button" className="secondary" onClick={() => setVisible((v) => !v)}>
        {visible ? "Hide code" : "View code"}
      </button>

      {visible && (
        <div className="code-viewer">
          <div className="code-viewer-bar">
            <span>train.py</span>
            <button type="button" className="secondary" onClick={copy}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <pre className="code-block">
            <code>{code}</code>
          </pre>
        </div>
      )}
    </>
  );
}
