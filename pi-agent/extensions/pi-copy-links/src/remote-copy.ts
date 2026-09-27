/**
 * pi-copy-links 复制出口的 SSH 感知分流。
 *
 * 若 pi-remote-clipboard 扩展已加载，点击 [复制] 时先问它：当前输入是否来自
 * SSH 客户端；是则它已通过 OSC 52 写入对端剪贴板并返回 true，本地原生
 * copyToClipboard 不再执行。未加载或当前是本地会话时完全走原生路径。
 *
 * 查找发生在点击时刻，与两个扩展的加载顺序无关。
 */
export function remoteCopySink(): ((text: string) => Promise<boolean>) | undefined {
  const value = (globalThis as Record<symbol, unknown>)[Symbol.for("pi-remote-clipboard:copy-sink")];
  return typeof value === "function" ? (value as (text: string) => Promise<boolean>) : undefined;
}
