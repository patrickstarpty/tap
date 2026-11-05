/** Four soft tones (orange, teal, slate, sand); a name always gets the same
 * one so an Agent or person is recognisable across cards, chart and drawer. */
const TONES = 4;

export function avatarTone(name: string): number {
  let hash = 0;
  for (const char of name) hash = (hash * 31 + (char.codePointAt(0) ?? 0)) | 0;
  return Math.abs(hash) % TONES;
}

export function initialOf(name: string): string {
  return (Array.from(name.trim())[0] ?? "").toUpperCase();
}

export function Avatar({
  name,
  size = "md",
}: {
  name: string;
  size?: "xs" | "sm" | "md" | "lg";
}) {
  return (
    <span
      className="tap-team-avatar"
      data-tone={avatarTone(name)}
      data-size={size}
      aria-hidden="true"
    >
      {initialOf(name)}
    </span>
  );
}
