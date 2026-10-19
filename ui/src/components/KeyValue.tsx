import { Fragment, type ReactNode } from "react";

export interface KeyValueRow {
  label: string;
  value: ReactNode;
}

/** Label/value pairs on a fixed label column. Pass `-` yourself for holes. */
export function KeyValue({ rows, className = "" }: { rows: KeyValueRow[]; className?: string }) {
  return (
    <dl className={`grid grid-cols-[7.5rem_minmax(0,1fr)] gap-x-3 ${className}`}>
      {rows.map((row) => (
        <Fragment key={row.label}>
          <dt className="microlabel self-baseline pt-[3px]">{row.label}</dt>
          <dd className="min-w-0 break-words">{row.value === "" ? "-" : row.value}</dd>
        </Fragment>
      ))}
    </dl>
  );
}
