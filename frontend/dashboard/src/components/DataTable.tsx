import type { ComponentChildren } from "preact";
import { useId } from "preact/hooks";

export interface Column {
  label: string;
  /** Numbers and money: right-aligned with tabular figures. */
  num?: boolean;
  /** A secondary column that a narrow card drops (its content folds under the row header via .narrow-only). */
  wideOnly?: boolean;
}

/**
 * The dashboard's one data table: a captioned <table> with a sticky header row inside a scroll box, so a wide
 * table scrolls sideways inside its card and never the page. The scroll box is a labelled, focusable region,
 * so keyboard users can scroll it too. Rows come in as children (<tr> with a <th scope="row"> first).
 */
export function DataTable(props: { caption: string; columns: Column[]; class?: string; children: ComponentChildren }) {
  const captionId = useId();
  const thClass = (c: Column) => [c.num && "num", c.wideOnly && "wide-only"].filter(Boolean).join(" ") || undefined;
  return (
    <div class="table-wrap" role="region" aria-labelledby={captionId} tabIndex={0}>
      <table class={props.class ? `table ${props.class}` : "table"}>
        <caption id={captionId}>{props.caption}</caption>
        <thead>
          <tr>
            {props.columns.map((c) => (
              <th key={c.label} scope="col" class={thClass(c)}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{props.children}</tbody>
      </table>
    </div>
  );
}

