import { useEffect, type RefObject } from "react";

export function useDialogFocus(ref: RefObject<HTMLDivElement | null>, open: boolean, close: () => void) {
  useEffect(() => {
    const dialog = ref.current;
    if (!open || !dialog) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusable = () => Array.from(dialog.querySelectorAll<HTMLElement>(
      'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex="0"]',
    )).filter(node => node.getClientRects().length > 0);
    (focusable()[0] ?? dialog).focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); }
      if (event.key !== "Tab") return;
      const nodes = focusable();
      const first = nodes[0] ?? dialog;
      const last = nodes[nodes.length - 1] ?? dialog;
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog)) {
        event.preventDefault(); first.focus();
      }
    };
    const onFocus = (event: FocusEvent) => {
      if (!dialog.contains(event.target as Node)) (focusable()[0] ?? dialog).focus();
    };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("focusin", onFocus);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("focusin", onFocus);
      if (previous?.isConnected) previous.focus();
    };
    // The modal lifetime owns focus, not the current frame or close callback.
  }, [ref, open]);
}
