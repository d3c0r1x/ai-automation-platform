import type { TaskView } from "../api";

const HIDDEN_COLUMNS = new Set(["url", "id", "why", "hits"]);

/**
 * Результат задачи: сводка, таблица и служебные пометки.
 *
 * Сводка показывается как есть — вместе с оговорками инструментов
 * (демо-источник, сымитированная доставка). Прятать их было бы удобнее,
 * но тогда панель выглядела бы убедительнее, чем результат на самом деле.
 */
export function ResultPanel({ view }: { view: TaskView }) {
  const result = view.task.result;
  if (!result) {
    return <p className="muted">Результат появится после выполнения задачи.</p>;
  }

  const columns = result.rows.length > 0
    ? Object.keys(result.rows[0]).filter((key) => !HIDDEN_COLUMNS.has(key))
    : [];

  return (
    <div className="result">
      <h3>Результат</h3>
      <p className="summary">{result.summary}</p>

      {columns.length > 0 && (
        <table>
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row, index) => (
              <tr key={index}>
                {columns.map((column) => (
                  <td key={column}>{formatCell(row[column])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <dl className="details">
        {Object.entries(result.data).map(([key, value]) => (
          <div key={key}>
            <dt>{key}</dt>
            <dd>{formatCell(value)}</dd>
          </div>
        ))}
        {Object.entries(result.artifacts).map(([name, path]) => (
          <div key={name}>
            <dt>файл</dt>
            <dd>
              {name} → {path}
            </dd>
          </div>
        ))}
        {Object.keys(result.delivery).length > 0 && (
          <div>
            <dt>доставка</dt>
            <dd>{formatCell(result.delivery)}</dd>
          </div>
        )}
      </dl>
    </div>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
