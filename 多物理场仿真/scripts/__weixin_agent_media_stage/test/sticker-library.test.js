import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { CustomStickerLibrary, extractStickerDirective } from "../src/sticker-library.js";

const ONE_PIXEL_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+R4n0WQAAAABJRU5ErkJggg==",
  "base64",
);

function configFor(root) {
  return {
    customStickersEnabled: true,
    customStickersDir: path.join(root, "custom"),
    customStickerCacheDir: path.join(root, "cache"),
    customStickerContentScale: 0.62,
    customStickerVisualFill: 0.82,
    customStickerCanvasSize: 512,
    pythonCommand: "py",
    toolsDir: path.resolve("tools"),
    projectDir: path.resolve("."),
  };
}

test("custom sticker filenames become safe model-selectable scene tags", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "vera-sticker-library-"));
  const config = configFor(root);
  fs.mkdirSync(config.customStickersDir, { recursive: true });
  const source = path.join(config.customStickersDir, "开心_夸奖_得意.png");
  fs.writeFileSync(source, ONE_PIXEL_PNG);
  let processArgs;
  const library = new CustomStickerLibrary(config, {
    runImpl: async (_command, args) => {
      processArgs = args;
      return { stdout: JSON.stringify({ path: path.join(config.customStickerCacheDir, "ready.png") }) };
    },
  });
  try {
    assert.deepEqual(library.entries()[0].tags, ["开心", "夸奖", "得意"]);
    assert.match(library.promptPolicy(), /开心、夸奖、得意/);
    const selected = await library.prepare("夸奖", "stable-job");
    assert.equal(selected.file, "开心_夸奖_得意.png");
    assert.ok(processArgs.includes("0.62"));
    assert.ok(processArgs.includes("0.82"));
    assert.deepEqual(extractStickerDirective("做得不错。\n\n[[VERA_STICKER:夸奖]]"), {
      text: "做得不错。",
      tag: "夸奖",
    });
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
