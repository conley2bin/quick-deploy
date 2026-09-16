declare module "@earendil-works/pi-tui" {
  export interface Component {
    render(width: number): string[];
    invalidate(): void;
  }
  export interface TUI extends Component {
    children: Component[];
    terminal: { columns: number };
    requestRender(force?: boolean): void;
  }
  export function allocateImageId(): number;
  export function getCapabilities(): { images: "kitty" | "iterm2" | null; trueColor: boolean; hyperlinks: boolean };
  export function getCellDimensions(): { widthPx: number; heightPx: number };
}

declare module "@earendil-works/pi-coding-agent" {
  export const VERSION: string;
  export type SessionEntry = {
    type: string;
    customType?: string;
    data?: unknown;
    message?: unknown;
    [key: string]: unknown;
  };
  export function sessionEntryToContextMessages(entry: SessionEntry): unknown[];
  export interface MarkdownContext {
    messageType: "user" | "assistant" | "assistant-thinking";
    isStreaming: boolean;
    availableWidth: number;
  }
  export interface EventBus {
    emit(channel: string, data: unknown): void;
    on(channel: string, handler: (data: unknown) => void): () => void;
  }
  export interface ExtensionAPI {
    events: EventBus;
    registerMarkdownTransformer(transformer: (markdown: string, context: MarkdownContext) => string): void;
    on(event: string, handler: (event: any, context: any) => unknown): void;
  }
}
