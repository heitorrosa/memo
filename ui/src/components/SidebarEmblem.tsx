import { LOGO_MASK } from "../ascii/logo-mask";

const EMBLEM = LOGO_MASK.map((row) =>
  row.replace(/[#+. ]/g, (cell) => (cell === "#" ? "█" : cell === "+" ? "▒" : " ")),
).join("\n");

export function SidebarEmblem({ className = "" }: { className?: string }) {
  return (
    <pre
      aria-hidden="true"
      className={`select-none whitespace-pre text-[4px] leading-[4px] tracking-normal text-amber/90 ${className}`}
    >
      {EMBLEM}
    </pre>
  );
}
