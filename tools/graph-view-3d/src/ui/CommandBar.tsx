import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { useEffect, useRef } from "react";
import "@xterm/xterm/css/xterm.css";

interface Props {
  onCommand: (line: string) => string;
}

// xterm command bar: same state as the HUD panels (focus / filter / ns / help).
export default function CommandBar({ onCommand }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const handler = useRef(onCommand);
  handler.current = onCommand;

  useEffect(() => {
    if (!host.current) return;
    const term = new Terminal({
      convertEol: true,
      cursorBlink: true,
      fontSize: 12,
      fontFamily: 'ui-monospace, "Cascadia Mono", Consolas, monospace',
      theme: {
        background: "#050912",
        foreground: "#c7d2fe",
        cursor: "#22d3ee",
        selectionBackground: "#164e63",
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host.current);
    try {
      fit.fit();
    } catch {
      /* container not laid out yet */
    }

    const prompt = () => term.write("\r\n\x1b[36mmemo>\x1b[0m ");
    term.writeln("\x1b[36mmemo graph 3d\x1b[0m - command bar. type \x1b[1mhelp\x1b[0m for the command list.");
    prompt();

    let buf = "";
    term.onData((data) => {
      if (data === "\r") {
        const line = buf;
        buf = "";
        term.write("\r\n");
        const out = handler.current(line);
        if (out) term.write(out.replace(/\n/g, "\r\n"));
        prompt();
      } else if (data === "\u007f") {
        if (buf.length) {
          buf = buf.slice(0, -1);
          term.write("\b \b");
        }
      } else if (data >= " " && data !== "\u001b") {
        buf += data;
        term.write(data);
      }
    });

    const ro = new ResizeObserver(() => {
      try {
        fit.fit();
      } catch {
        /* ignore */
      }
    });
    ro.observe(host.current);
    return () => {
      ro.disconnect();
      term.dispose();
    };
  }, []);

  return <div ref={host} className="pointer-events-auto h-full w-full overflow-hidden rounded-lg border border-cyan-400/20 bg-[#050912]" />;
}
