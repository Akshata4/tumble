export default function DatasetTable({ data, selected, onSelect }) {
  if (!data) return null;
  const { headers, rows } = data;

  return (
    <div className="table-scroll">
      <table className="mini-table">
        <thead>
          <tr>
            {headers.map((h, i) => (
              <th
                key={i}
                className={`${onSelect ? "selectable" : ""} ${h === selected ? "selected" : ""}`}
                onClick={onSelect ? () => onSelect(h) : undefined}
                title={onSelect ? `Use "${h}" as the target column` : undefined}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j} className={headers[j] === selected ? "selected" : ""}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
