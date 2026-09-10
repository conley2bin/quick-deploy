// Derived from pi-tmux-images 0.2.0 (MIT).
// See LICENSE and UPSTREAM.md in this directory.

const ESC = "\x1b";
export const PLACEHOLDER_GLYPH = "\u{10eeee}";

// The stage-1 prototype emits at most four columns, two rows, and uses high
// image-ID byte 7. Keep only the marks exercised by that bounded mechanism.
export const ROW_COLUMN_DIACRITICS = [
  "\u{305}",
  "\u{30d}",
  "\u{30e}",
  "\u{310}",
  "\u{312}",
  "\u{33d}",
  "\u{33e}",
  "\u{33f}",
] as const;

function mark(index: number): string {
  const value = ROW_COLUMN_DIACRITICS[index];
  if (!value) throw new Error(`Kitty placeholder index ${index} exceeds the prototype table`);
  return value;
}

function rgb(code: 38 | 58, value: number): string {
  return `${ESC}[${code};2;${(value >>> 16) & 255};${(value >>> 8) & 255};${value & 255}m`;
}

/** Kitty Unicode placeholder: foreground is image ID, underline is placement ID. */
export function cell(column: number, row: number, imageId: number): string {
  const high = imageId >>> 24;
  const low = imageId & 0xffffff;
  return `${rgb(38, low)}${rgb(58, low || 1)}${PLACEHOLDER_GLYPH}${mark(row)}${mark(column)}${
    high ? mark(high) : ""
  }${ESC}[39;59m`;
}

/**
 * Produce placeholder rows that remain independent through Pi 0.85.1 wrapping.
 *
 * Pi's ANSI tracker understands 38/48 extended colors but not Kitty's 58
 * underline-color selector. Without a full row reset it mistakes the `2` in
 * `58;2;R;G;B` for SGR dim and prepends `ESC[2m` to the next Markdown line.
 * Ending each bounded row with SGR 0 prevents that synthetic style from
 * crossing the newline while leaving every cell's Kitty colors intact.
 */
export function grid(columns: number, rows: number, imageId: number): string[] {
  return Array.from({ length: rows }, (_, row) => {
    const cells = Array.from({ length: columns }, (_, column) => cell(column, row, imageId)).join("");
    return `${cells}${ESC}[0m`;
  });
}
