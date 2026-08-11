import fs from "node:fs";
import path from "node:path";

function safeStickerPath(root, relativePath) {
  const resolved = path.resolve(root, relativePath);
  const relative = path.relative(path.resolve(root), resolved);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error("表情包路径越界");
  }
  return resolved;
}

export class StickerTool {
  constructor(config) {
    this.name = "sticker";
    this.config = config;
    this.manifestFile = path.join(config.stickersDir, "manifest.json");
  }

  manifest() {
    try {
      const payload = JSON.parse(fs.readFileSync(this.manifestFile, "utf8"));
      return Array.isArray(payload.stickers) ? payload.stickers : [];
    } catch {
      return [];
    }
  }

  route(text) {
    const normalized = String(text || "").trim();
    const command = normalized.match(/^\/sticker(?:\s+([a-z0-9_-]+))?$/i);
    if (command) {
      const id = (command[1] || "random").toLowerCase();
      if (id === "list") {
        return { action: "list", args: {}, acknowledgement: "我看看现在有哪些表情。" };
      }
      return { action: "send", args: { id }, silentAcknowledgement: true };
    }
    return null;
  }

  async ensureLocalSticker(selected) {
    const imagePath = safeStickerPath(this.config.stickersDir, selected.file);
    if (fs.existsSync(imagePath)) return imagePath;
    if (!selected.url) throw new Error(`表情包文件不存在: ${selected.file}`);
    const url = new URL(selected.url);
    const allowedHosts = new Set(["raw.githubusercontent.com", "cdn.jsdelivr.net"]);
    if (url.protocol !== "https:" || !allowedHosts.has(url.hostname)) {
      throw new Error("拒绝未知表情包下载地址");
    }
    const response = await fetch(url, { signal: AbortSignal.timeout(15_000) });
    if (!response.ok) throw new Error(`表情包下载 HTTP ${response.status}`);
    const buffer = Buffer.from(await response.arrayBuffer());
    if (buffer.length > 1024 * 1024 || buffer.subarray(1, 4).toString("ascii") !== "PNG") {
      throw new Error("表情包下载内容不是有效的小型 PNG");
    }
    fs.mkdirSync(path.dirname(imagePath), { recursive: true });
    const temp = `${imagePath}.tmp`;
    fs.writeFileSync(temp, buffer);
    fs.renameSync(temp, imagePath);
    return imagePath;
  }

  async execute({ job }) {
    const stickers = this.manifest().filter((item) => item?.id && (item?.text || item?.file));
    if (job.payload.action === "list") {
      const list = stickers.map((item) => `${item.text || ""} ${item.label || item.id}`.trim()).join("、");
      return {
        reply: list ? `现在有这些表情：${list}` : "表情库还是空的。",
        model: "sticker:local",
        usage: {},
      };
    }
    if (!stickers.length) {
      const error = new Error("表情包库为空");
      error.retryable = false;
      error.userMessage = "表情包发送通道已经就绪，但本地表情包库还是空的。";
      throw error;
    }
    const requested = String(job.payload.args?.id || "random").toLowerCase();
    const candidates = requested === "random"
      ? stickers
      : stickers.filter((item) => item.id === requested || item.moods?.includes(requested));
    const pool = candidates.length ? candidates : stickers;
    const selected = pool[Math.floor(Math.random() * pool.length)];
    if (selected.text) {
      return {
        reply: String(selected.text),
        stickerId: selected.id,
        model: "sticker:inline-emoji",
        usage: {},
      };
    }
    let imagePath;
    try {
      imagePath = await this.ensureLocalSticker(selected);
    } catch (cause) {
      const error = new Error(`表情包文件不存在: ${selected.file}`);
      error.retryable = false;
      error.userMessage = "选中的表情包还没有下载成功，请稍后再试。";
      error.cause = cause;
      throw error;
    }
    return {
      reply: "",
      imagePath,
      stickerId: selected.id,
      model: "sticker:local",
      usage: {},
    };
  }
}
