import { Button } from "antd";
import { useEffect, useRef, useState } from "react";

export interface GovernedModel {
  readonly alias: string;
  readonly displayName: string;
  readonly capabilities: readonly string[];
}

export function ModelSelector({
  models,
  value,
  onChange,
  label,
  menuLabel = "Model",
}: {
  models: readonly GovernedModel[];
  value: string;
  onChange: (alias: string) => void;
  label?: (displayName: string) => string;
  menuLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const selected = models.find((model) => model.alias === value);

  useEffect(() => {
    if (open)
      menu.current
        ?.querySelector<HTMLButtonElement>(
          "[role='menuitemradio'][aria-checked='true']",
        )
        ?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target))
        setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);

  if (selected === undefined)
    return <span role="status">Model unavailable</span>;
  return (
    <div ref={root} className="tap-composer-model-control">
      <Button
        ref={trigger}
        className="tap-model-trigger"
        aria-label={label?.(selected.displayName)}
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((current) => !current)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            setOpen(true);
          }
        }}
      >
        {selected.displayName}
      </Button>
      {open && (
        <div
          ref={menu}
          role="menu"
          aria-label={menuLabel}
          className="tap-model-menu"
          onKeyDown={(event) => {
            const items = Array.from(
              menu.current?.querySelectorAll<HTMLButtonElement>(
                "[role='menuitemradio']",
              ) ?? [],
            );
            const current = items.indexOf(
              document.activeElement as HTMLButtonElement,
            );
            const positions: Record<string, number> = {
              ArrowDown: (current + 1) % items.length,
              ArrowUp: (current - 1 + items.length) % items.length,
              Home: 0,
              End: items.length - 1,
            };
            if (event.key in positions) {
              event.preventDefault();
              items[positions[event.key]]?.focus();
            }
            if (event.key === "Escape") {
              event.preventDefault();
              setOpen(false);
              trigger.current?.focus();
            }
            if (event.key === "Tab") setOpen(false);
          }}
        >
          {models.map((model) => (
            <button
              key={model.alias}
              type="button"
              role="menuitemradio"
              tabIndex={-1}
              aria-checked={model.alias === value}
              onClick={() => {
                onChange(model.alias);
                setOpen(false);
                trigger.current?.focus();
              }}
            >
              {model.displayName}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
