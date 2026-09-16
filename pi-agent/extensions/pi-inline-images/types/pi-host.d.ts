declare module "@earendil-works/pi-tui" {
  export function allocateImageId(): number;
  export function getCellDimensions(): { widthPx: number; heightPx: number };
}

declare module "@earendil-works/pi-coding-agent" {
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
