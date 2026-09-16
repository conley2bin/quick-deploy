import { spawn } from "node:child_process";

/** Model-supplied URLs are data, never shell commands or executable URI schemes. */
export function webUrl(value: string): string | undefined {
  if (/[\x00-\x20\x7f]/u.test(value)) return undefined;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : undefined;
  } catch { return undefined; }
}

export function browserCommand(url: string, platform = process.platform): [string, string[]] {
  const target = webUrl(url);
  if (!target) throw new Error("只支持有效的 HTTP(S) 链接");
  if (platform === "darwin") return ["open", [target]];
  if (platform === "win32") return ["rundll32.exe", ["url.dll,FileProtocolHandler", target]];
  return ["xdg-open", [target]];
}

export async function openWebUrl(url: string): Promise<void> {
  const [command, args] = browserCommand(url);
  await new Promise<void>((resolve, reject) => {
    const child = spawn(command, args, { stdio: "ignore", shell: false, detached: true });
    child.unref(); // Some xdg-open browser processes live until the browser closes.
    child.once("error", (error) => reject(new Error(`${command}: ${error.message}`)));
    child.once("exit", (code, signal) => {
      if (code === 0) resolve();
      else reject(new Error(`${command} ${signal ? `被 ${signal} 中止` : `退出码 ${code}`}`));
    });
  });
}
