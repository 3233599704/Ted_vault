import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { runProcess } from "./process-runner.js";

const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".webp"]);
const DIRECTIVE_RE = /\[\[VERA_STICKER:([^\]\r\n]{1,32})\]\]/gu;

function safeTag(raw) {
  const value = String(raw || "").trim();
  return /^[\p{L}\p{N}]{1,16}$/u.test(value) ? value : "";
}

function tagsFromFile(filePath) {
  const stem = path.basename(filePath, path.extname(filePath));
  return [...new Set(
    stem.split(/[_\-\s,，]+/u).map(safeTag).filter(Boolean),
  )].slice(0, 8);
}

function collectFiles(root) {
  if (!fs.existsSync(root)) return [];
  const files = [];
  const visit = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (entry.name.startsWith(".")) continue;
      const fullPath = path.join(directory, entry.name);
      if (entry.isDirectory()) visit(fullPath);
      else if (entry.isFile() && IMAGE_EXTENSIONS.has(path.extname(entry.name).toLowerCase())) {
        files.push(fullPath);
      }
    }
  };
  visit(root);
  return files;
}

export function extractStickerDirective(rawText) {
  const text = String(rawText || "");
  let tag = "";
  for (const match of text.matchAll(DIRECTIVE_RE)) tag = safeTag(match[1]);
  const cleaned = text
    .replace(DIRECTIVE_RE, "")
    .replace(/\n[\t ]*\n[\t ]*\n+/g, "\n\n")
    .trim();
  return { text: cleaned, tag };
}

export class CustomStickerLibrary {
  constructor(config, options = {}) {
    this.config = config;
    this.runImpl = options.runImpl || runProcess;
  }

  entries() {
    if (!this.config.customStickersEnabled) return [];
    return collectFiles(this.config.customStickersDir).map((sourcePath) => ({
      sourcePath,
      file: path.relative(this.config.customStickersDir, sourcePath),
      tags: tagsFromFile(sourcePath),
    })).filter((item) => item.tags.length);
  }

  promptPolicy() {
    const tags = [...new Set(this.entries().flatMap((item) => item.tags))].slice(0, 48);
    if (!tags.length) return "";
    return [
      "你有一个私人表情图片库。只有当图片确实比单纯文字更贴合当前情绪时，才可在正常回复末尾追加一个隐藏指令。",
      `格式必须是 [[VERA_STICKER:标签]]，标签只能从以下选一个：${tags.join("、")}`,
      "隐藏指令不会展示给用户；每次最多一个，不要为了使用表情包而使用，也不要在正文中提到这个指令。",
    ].join("\n");
  }

  select(tag, seed = "") {
    const normalized = safeTag(tag);
    const matches = this.entries().filter((item) => item.tags.includes(normalized));
    if (!matches.length) return null;
    const digest = crypto.createHash("sha256").update(String(seed)).digest();
    return matches[digest.readUInt32BE(0) % matches.length];
  }

  async prepare(tag, seed = "") {
    const selected = this.select(tag, seed);
    if (!selected) return null;
    const result = await this.runImpl(
      this.config.pythonCommand,
      [
        path.join(this.config.toolsDir, "prepare_sticker.py"),
        "--source", selected.sourcePath,
        "--root", this.config.customStickersDir,
        "--cache", this.config.customStickerCacheDir,
        "--scale", String(this.config.customStickerContentScale),
        "--fill", String(this.config.customStickerVisualFill),
        "--canvas", String(this.config.customStickerCanvasSize),
      ],
      {
        cwd: this.config.projectDir,
        timeoutMs: 30_000,
        env: { ...process.env, PYTHONIOENCODING: "utf-8" },
      },
    );
    const payload = JSON.parse(result.stdout);
    return { ...selected, preparedPath: payload.path, metadata: payload };
  }
}
