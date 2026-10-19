import { useEffect, useRef, type ReactNode } from "react";

export interface Column<T> {
  label: string;
  width?: string;
  align?: "left" | "right";
  render: (row: T) => ReactNode;
}

export function Table<T>({
  columns,
  rows,
  rowKey,
  selected,
  onSelect,
  rowClassName,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  selected: string | null;
  onSelect: (key: string) => void;
  rowClassName?: (row: T) => string;
}) {
  const template = columns.map((column) => column.width ?? "minmax(0,1fr)").join(" ");
  const selectedRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    selectedRef.current?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  return (
    <div className="pr-2">
      <div
        aria-hidden="true"
        className="microlabel grid items-center gap-x-3 border-b border-line-soft px-3 pb-1"
        style={{ gridTemplateColumns: template }}
      >
        {columns.map((column, index) => (
          <span
            key={`${column.label}:${index}`}
            className={column.align === "right" ? "text-right" : undefined}
          >
            {column.label}
          </span>
        ))}
      </div>
      {rows.map((row) => {
        const key = rowKey(row);
        const isSelected = key === selected;
        return (
          <button
            key={key}
            type="button"
            aria-current={isSelected}
            ref={isSelected ? selectedRef : undefined}
            onClick={() => onSelect(key)}
            className={`relative grid w-full items-center gap-x-3 border-b border-line-soft px-3 py-0.5 hover:bg-surface-2 ${
              isSelected ? "bg-surface" : ""
            } ${rowClassName ? rowClassName(row) : ""}`}
            style={{ gridTemplateColumns: template }}
          >
            {isSelected ? (
              <span
                aria-hidden="true"
                className="absolute bottom-0 left-0 top-0 w-px bg-amber"
              />
            ) : null}
            {columns.map((column, index) => (
              <span
                key={`${column.label}:${index}`}
                className={`min-w-0 truncate ${
                  column.align === "right" ? "tabular text-right" : ""
                }`}
              >
                {column.render(row)}
              </span>
            ))}
          </button>
        );
      })}
    </div>
  );
}
