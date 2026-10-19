import { motion } from "motion/react";

export type ToastKind = "ok" | "err";

export interface ToastMsg {
  kind: ToastKind;
  text: string;
}

/** Status-line flash shown for ~3s after a write (App owns the timer). */
export function Toast({ toast }: { toast: ToastMsg }) {
  const isOk = toast.kind === "ok";
  return (
    <motion.span
      initial={{ opacity: 0, y: -3 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.16, ease: "easeOut" }}
      className={`truncate ${isOk ? "text-green" : "text-coral"}`}
    >
      {isOk ? "✓" : "✗"} {toast.text}
    </motion.span>
  );
}
