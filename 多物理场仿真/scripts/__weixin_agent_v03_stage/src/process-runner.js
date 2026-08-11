import { spawn } from "node:child_process";

export function runProcess(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      env: options.env || process.env,
      shell: false,
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
    });
    const stdout = [];
    const stderr = [];
    let bytes = 0;
    let settled = false;
    const maxBuffer = options.maxBuffer || 2 * 1024 * 1024;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error);
      else resolve(value);
    };
    const collect = (target) => (chunk) => {
      bytes += chunk.length;
      if (bytes > maxBuffer) {
        child.kill();
        finish(new Error(`子进程输出超过 ${maxBuffer} 字节`));
        return;
      }
      target.push(chunk);
    };
    child.stdout.on("data", collect(stdout));
    child.stderr.on("data", collect(stderr));
    child.once("error", (error) => finish(error));
    child.once("close", (code) => {
      const result = {
        code,
        stdout: Buffer.concat(stdout).toString("utf8").trim(),
        stderr: Buffer.concat(stderr).toString("utf8").trim(),
      };
      if (code === 0) finish(null, result);
      else finish(new Error(`${command} 退出码 ${code}: ${result.stderr || result.stdout}`));
    });
    const timer = setTimeout(() => {
      child.kill();
      finish(new Error(`${command} 执行超过 ${options.timeoutMs || 60_000}ms`));
    }, options.timeoutMs || 60_000);
    if (options.input != null) child.stdin.end(String(options.input));
    else child.stdin.end();
  });
}
