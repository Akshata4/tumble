import ReactMarkdown from "react-markdown";

// No rehype-raw/rehype-html plugin here on purpose: react-markdown then
// renders any literal HTML in the report (e.g. a stray "<script>" an
// agent's report text happened to contain) as inert text, not markup --
// the report's content ultimately traces back to an uploaded dataset in
// the multi-tenant server case, so it isn't a trusted input.
export default function MarkdownReport({ content }) {
  return (
    <div className="report">
      <ReactMarkdown>{content}</ReactMarkdown>
    </div>
  );
}
