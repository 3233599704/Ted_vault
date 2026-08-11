import path from "node:path";
import { fileURLToPath } from "node:url";

import { runProcess } from "./process-runner.js";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const DPAPI_SCRIPT = path.join(SCRIPT_DIR, "dpapi.ps1");
const POWERSHELL = path.join(
  process.env.SystemRoot || "C:\\Windows",
  "System32",
  "WindowsPowerShell",
  "v1.0",
  "powershell.exe",
);

async function transform(mode, text, runImpl = runProcess) {
  const result = await runImpl(
    POWERSHELL,
    ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", DPAPI_SCRIPT, mode],
    { input: String(text), timeoutMs: 30_000, maxBuffer: 2 * 1024 * 1024 },
  );
  return result.stdout;
}

export function protectText(text, runImpl) {
  return transform("protect", text, runImpl);
}

export function unprotectText(text, runImpl) {
  return transform("unprotect", text, runImpl);
}
