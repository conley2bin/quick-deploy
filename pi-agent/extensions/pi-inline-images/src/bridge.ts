import type { EventBus } from "@earendil-works/pi-coding-agent";
import type { LoadedImage } from "./images.ts";
import type { TerminalImages } from "./terminal.ts";

export const IMAGE_BRIDGE_VERSION = 1;
export const IMAGE_BRIDGE_REQUEST = "pi-inline-images:graphics-owner:request";
export const IMAGE_BRIDGE_REPLY = "pi-inline-images:graphics-owner:reply";

type Owner = "inline" | "read";
type Request = { version: number; owner: Owner; requestId: string };
type Reply = Request & { handle?: GraphicsOwnerHandle; error?: string };

/** Owner-scoped public event-bus handle; no process-wide stdout interception. */
export interface GraphicsOwnerHandle {
  readonly version: typeof IMAGE_BRIDGE_VERSION;
  readonly owner: Owner;
  prepare(logicalId: string, image: LoadedImage): Promise<void>;
  render(logicalId: string, width: number): string[];
  failure(logicalId: string): string | undefined;
  release(logicalId: string): Promise<void>;
  reset(): Promise<void>;
}

function request(value: unknown): value is Request {
  return Boolean(value) && typeof value === "object"
    && (value as Partial<Request>).version === IMAGE_BRIDGE_VERSION
    && ((value as Partial<Request>).owner === "inline" || (value as Partial<Request>).owner === "read")
    && typeof (value as Partial<Request>).requestId === "string";
}

/** Publishes exactly two handles backed by the one inline graphics transport. */
export function installGraphicsBridge(events: EventBus, terminal: TerminalImages, resourcesChanged: () => void = () => {}): () => void {
  const lifecycle = async (operation: () => Promise<void>): Promise<void> => {
    try { await operation(); } finally { resourcesChanged(); }
  };
  const makeHandle = (owner: Owner): GraphicsOwnerHandle => ({
    version: IMAGE_BRIDGE_VERSION,
    owner,
    prepare: (logicalId, image) => lifecycle(() => terminal.prepare(`${owner}:${logicalId}`, image)),
    render: (logicalId, width) => terminal.render(`${owner}:${logicalId}`, width),
    failure: (logicalId) => terminal.failure(`${owner}:${logicalId}`),
    release: (logicalId) => lifecycle(() => terminal.release(`${owner}:${logicalId}`)),
    reset: () => lifecycle(() => terminal.resetOwner(owner)),
  });
  const handles = new Map<Owner, GraphicsOwnerHandle>([["inline", makeHandle("inline")], ["read", makeHandle("read")]]);
  return events.on(IMAGE_BRIDGE_REQUEST, (value: unknown) => {
    if (!request(value)) return;
    const reply: Reply = { ...value, handle: handles.get(value.owner) };
    events.emit(IMAGE_BRIDGE_REPLY, reply);
  });
}
