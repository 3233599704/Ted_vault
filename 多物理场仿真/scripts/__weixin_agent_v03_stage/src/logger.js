import fs from "node:fs";
import path from "node:path";

function stamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function createLogger(logFile, options = {}) {
  const maxBytes = options.maxBytes || 5 * 1024 * 1024;
  fs.mkdirSync(path.dirname(logFile), { recursive: true });

  function rotateIfNeeded() {
    try {
      if (fs.statSync(logFile).size < maxBytes) return;
      const backup = `${logFile}.1`;
      fs.rmSync(backup, { force: true });
      fs.renameSync(logFile, backup);
    } catch {
      // The file may not exist yet or may be temporarily locked.
    }
  }

  return function log(message) {
    const line = `[${stamp()}] ${message}`;
    console.log(line);
    try {
      rotateIfNeeded();
      fs.appendFileSync(logFile, `${line}\n`, "utf8");
    } catch {
      // Logging must never stop message delivery.
    }
  };
}

