import { hasUriScheme } from "./links.ts";

export interface PathSpan {
  readonly start: number;
  readonly end: number;
  /** Encoded local href, consumed by the existing click-time target parser. */
  readonly href: string;
}

// File evidence, not arbitrary dotted prose/TLDs or version numbers. Explicit
// /, ./ and ../ paths do not need an extension; relative directories end in /.
const EXTENSIONS = new Set((
  "c h cc cpp cxx hpp cs fs fsx go rs java kt kts scala swift m mm py pyi pyw ipynb r jl lua rb php pl pm sh bash zsh fish ps1 bat cmd " +
  "js jsx mjs cjs ts tsx mts cts vue svelte html htm css scss sass less wasm sql graphql gql proto cu cuh asm s v vb vbs " +
  "txt md markdown mdx rst org tex bib adoc pdf doc docx odt rtf xls xlsx ods ppt pptx odp csv tsv json jsonl ndjson yaml yml toml ini cfg conf env xml log " +
  "png jpg jpeg gif webp svg bmp tif tiff ico avif heic raw psd eps mp4 webm mov mkv avi m4v mpeg mpg wmv flv mp3 wav flac ogg m4a aac opus " +
  "zip tar gz bz2 xz zst tgz 7z rar deb rpm apk exe dll so dylib o a bin iso img dmg whl egg lock map patch diff " +
  "npy npz parquet arrow feather h5 hdf5 hdf pkl pickle pt pth ckpt safetensors onnx pb tflite gguf sqlite sqlite3 db duckdb " +
  "stl obj ply glb gltf fbx blend 3mf urdf sdf dae usd usda usdc usdz bag mcap vtt srt ttf otf woff woff2"
).split(" "));
const BASENAMES = new Set(["Dockerfile", "Containerfile", "Makefile", "GNUmakefile", "CMakeLists.txt",
  "README", "LICENSE", "LICENCE", "COPYING", "NOTICE", "CHANGELOG", "Gemfile", "Rakefile", "Procfile",
  ".gitignore", ".gitattributes", ".editorconfig", ".env", ".npmrc", ".bashrc", ".zshrc"]);
const COMMAND = /^(?:(?:sudo|env|command|exec|nohup|time)\s+)*(?:cd|ls|cat|echo|printf|test|read|export|unset|eval|builtin|type|which|whereis|xargs|tee|cut|du|df|apt|apt-get|systemctl|journalctl|head|tail|less|more|file|stat|find|fd|rg|grep|sed|awk|sort|wc|cp|mv|rm|mkdir|touch|chmod|chown|ln|tar|unzip|curl|wget|ssh|scp|rsync|git|npm|npx|pnpm|yarn|bun|node|deno|python[\d.]*|pip[\d.]*|uv|conda|poetry|pytest|ruff|mypy|bash|sh|zsh|fish|source|ffmpeg|ffprobe|magick|convert|code|make|cmake|ninja|cargo|rustc|go|gcc|g\+\+|clang|java|javac|docker|podman|kubectl)(?:\s|$)/iu;

/** Syntax-only command evidence. Never run a shell or inspect a file to classify. */
export function commandLike(value: string): boolean {
  const line = value.trim().replace(/^(?:>\s*)*(?:[-*+]\s+|\d+[.)]\s+)?/u, "")
    .replace(/^(?:run|execute)\s+/iu, "");
  // A bordered Markdown table row is not a shell pipeline.
  if (/^\|.*\|$/u.test(line)) return false;
  return COMMAND.test(line) || /^\$\s/u.test(line) || /^[A-Za-z_][A-Za-z0-9_]*=/u.test(line) ||
    /(?:^|\s)--?[A-Za-z][\w-]*(?:\s|=|$)/u.test(line) || /(?:&&|\|\||\s[|<>]\s|\$\(|`[^`]*`\s*[|;])/u.test(line) ||
    /^(?:\.?\.?\/|\/)[^\s]+\.(?:sh|bash|py|js|mjs|cjs|pl|rb|exe)\s+\S/iu.test(line) ||
    /^\/(?:usr\/)?(?:local\/)?s?bin\/\S+\s+\S/u.test(line);
}

function literalHref(path: string): string {
  // Encode each component once, including all URI delimiters. Prefix relative
  // paths so a basename can never acquire scheme authority after encoding.
  const encoded = path.split("/").map((part) => encodeURIComponent(part)
    .replace(/[!'()*]/gu, (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`)).join("/");
  return path.startsWith("/") || path.startsWith("./") || path.startsWith("../") ? encoded : `./${encoded}`;
}

function crediblePath(value: string, spaces: boolean): boolean {
  if (!value || /[\x00-\x1f\x7f-\x9f\uD800-\uDFFF\\<>"'`{}\[\]|;$@]/u.test(value) ||
      (!spaces && /\s/u.test(value)) || value !== value.trim() || value.startsWith("-") ||
      value.startsWith("~") || value.startsWith("//") || hasUriScheme(value)) return false;
  if (value.includes("//")) return false;
  const rooted = value.startsWith("/") || value.startsWith("./") || value.startsWith("../");
  if (rooted) return true;
  if (/^(?:www\.|(?:[A-Za-z0-9-]+\.)+(?:com|net|org|dev|io|ai|co|app|edu|gov|cn|uk|de|jp)(?:\/|$))/iu.test(value)) return false;
  const basename = value.split("/").at(-1)!;
  if (value.endsWith("/")) return value.length > 1 && !/[?#%:]/u.test(value);
  if (BASENAMES.has(basename)) return true;
  // #, ? and % are filename data here, not selectors, queries or escapes.
  const extension = /\.([A-Za-z0-9]+)(?:[?#%].*)?$/u.exec(basename)?.[1]?.toLowerCase();
  return extension !== undefined && EXTENSIONS.has(extension) && (extension !== "org" || value.includes("/"));
}

/** Single-path code spans may contain spaces; commands are never scanned inside. */
export function inlineCodePaths(value: string): PathSpan[] {
  if (commandLike(value)) return [];
  const firstWord = value.split(/\s/u)[0]!;
  if (/\s/u.test(value) && (!firstWord.includes("/") || /\s\S*\//u.test(value))) return [];
  return crediblePath(value, true) ? [{ start: 0, end: value.length, href: literalHref(value) }] : [];
}

/** URI/email words are opaque, including punctuation-delimited pieces inside. */
export function nonPathTextRanges(value: string): Array<{ start: number; end: number }> {
  return [...value.matchAll(/(?:^|(?<=[\s(<]))(?:[A-Za-z][A-Za-z0-9+.-]*:\S+|www\.\S+|\S+@\S+)/gu)]
    .map((match) => ({ start: match.index, end: match.index + match[0].length }));
}

/** Plain text uses whole, punctuation-delimited words, never URL substrings. */
export function plainTextPaths(value: string): PathSpan[] {
  const paths: PathSpan[] = [];
  const urls = nonPathTextRanges(value);
  for (const match of value.matchAll(/[^\s<>"'`()\[\]{},;*|]+/gu)) {
    if (urls.some((url) => match.index >= url.start && match.index < url.end)) continue;
    const raw = match[0];
    // Sentence-ending dots/colon are punctuation in prose; code spans are exact.
    const path = raw.replace(/[.!:]+$/u, "");
    if (!crediblePath(path, false)) continue;
    paths.push({ start: match.index, end: match.index + path.length, href: literalHref(path) });
  }
  return paths;
}
