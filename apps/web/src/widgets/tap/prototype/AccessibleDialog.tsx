import { createPortal } from "react-dom";
import {
  useLayoutEffect,
  useRef,
  type KeyboardEvent,
  type ReactNode,
} from "react";

const FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[href]:not([aria-hidden="true"])',
  '[tabindex]:not([tabindex="-1"])',
].join(",");

interface AccessibleDialogProps {
  ariaLabel: string;
  children: ReactNode;
  className: string;
  onClose: () => void;
  opener: HTMLElement | null;
}

export function AccessibleDialog({
  ariaLabel,
  children,
  className,
  onClose,
  opener,
}: AccessibleDialogProps) {
  const dialogRef = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  const openerRef = useRef(opener);
  onCloseRef.current = onClose;
  openerRef.current = opener;

  useLayoutEffect(() => {
    const productShell =
      (openerRef.current?.closest(
        ".tap-product-shell",
      ) as HTMLElement | null) ??
      document.querySelector<HTMLElement>(".tap-product-shell");
    const previousAriaHidden =
      productShell?.getAttribute("aria-hidden") ?? null;
    const previouslyInert = productShell?.hasAttribute("inert") ?? false;

    dialogRef.current?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR)?.focus();
    productShell?.setAttribute("inert", "");
    productShell?.setAttribute("aria-hidden", "true");

    return () => {
      if (previousAriaHidden === null) {
        productShell?.removeAttribute("aria-hidden");
      } else {
        productShell?.setAttribute("aria-hidden", previousAriaHidden);
      }
      if (!previouslyInert) productShell?.removeAttribute("inert");
      openerRef.current?.focus();
    };
  }, []);

  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      onCloseRef.current();
      return;
    }
    if (event.key !== "Tab") return;

    const focusableElements = Array.from(
      dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR) ??
        [],
    );
    const firstElement = focusableElements[0];
    const lastElement = focusableElements.at(-1);

    if (
      event.shiftKey &&
      (document.activeElement === firstElement ||
        !dialogRef.current?.contains(document.activeElement))
    ) {
      event.preventDefault();
      lastElement?.focus();
    } else if (
      !event.shiftKey &&
      (document.activeElement === lastElement ||
        !dialogRef.current?.contains(document.activeElement))
    ) {
      event.preventDefault();
      firstElement?.focus();
    }
  };

  return createPortal(
    <div className="tap-picker-backdrop">
      <section
        ref={dialogRef}
        className={className}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
      >
        {children}
      </section>
    </div>,
    document.body,
  );
}
