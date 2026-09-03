interface PanelToggleIconProps {
  side: "left" | "right";
  state: "collapsed" | "expanded";
}

export function PanelToggleIcon({ side, state }: PanelToggleIconProps) {
  const expandedDividerX = side === "left" ? 7 : 13;
  const collapsedDividerX = side === "left" ? 4 : 16;
  const dividerX = state === "expanded" ? expandedDividerX : collapsedDividerX;

  return (
    <svg
      aria-hidden="true"
      data-panel-icon={side}
      data-panel-state={state}
      focusable="false"
      viewBox="0 0 20 20"
    >
      <rect x="2" y="3" width="16" height="14" rx="3" />
      <path d={`M${dividerX} 3.5v13`} />
    </svg>
  );
}
