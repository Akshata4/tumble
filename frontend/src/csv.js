// Minimal CSV line splitter -- handles basic double-quoted fields (with
// "" escaping) since real-world CSVs often have quoted text containing
// commas. This only feeds a cosmetic preview table (the backend does the
// real parsing with pandas for anything that matters), so it doesn't need
// to be a fully spec-compliant RFC 4180 parser.
function splitCsvLine(line) {
  const fields = [];
  let field = "";
  let inQuotes = false;

  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inQuotes) {
      if (c === '"' && line[i + 1] === '"') {
        field += '"';
        i++;
      } else if (c === '"') {
        inQuotes = false;
      } else {
        field += c;
      }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ",") {
      fields.push(field);
      field = "";
    } else {
      field += c;
    }
  }
  fields.push(field);
  return fields;
}

// Reads only the first `sampleBytes` of `file` -- enough for a header plus
// a handful of rows without pulling a multi-MB upload into memory just to
// preview it.
export async function readCsvPreview(file, { maxRows = 8, sampleBytes = 65536 } = {}) {
  const text = await file.slice(0, sampleBytes).text();
  const lines = text.split(/\r\n|\n/).filter((l) => l.length > 0);

  // The byte slice can cut the last line in half -- drop it unless it was
  // also the file's true end (sample came back shorter than requested).
  if (text.length >= sampleBytes && lines.length > 1) {
    lines.pop();
  }

  if (lines.length === 0) return null;
  const [headerLine, ...rowLines] = lines;
  return {
    headers: splitCsvLine(headerLine),
    rows: rowLines.slice(0, maxRows).map(splitCsvLine),
  };
}
