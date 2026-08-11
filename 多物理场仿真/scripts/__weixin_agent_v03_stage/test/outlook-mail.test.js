import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { OutlookMailboxPoller, formatOutlookMessage } from "../src/outlook-mail.js";

function message(id, receivedDateTime, body = "完整正文") {
  return {
    id,
    subject: `主题 ${id}`,
    from: { emailAddress: { name: "老师", address: "teacher@example.edu" } },
    receivedDateTime,
    body: { contentType: "text", content: body },
    bodyPreview: body.slice(0, 20),
    hasAttachments: false,
  };
}

test("Outlook poller establishes a baseline then emits each new mail once", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vera-outlook-mail-"));
  const stateFile = path.join(dir, "sync.json");
  const oldMail = message("old", "2026-07-10T01:00:00Z");
  const newMail = message("new", "2026-07-10T02:00:00Z", "第一行\r\n第二行");
  const replies = [
    new Response(JSON.stringify({ value: [oldMail] }), { status: 200 }),
    new Response(JSON.stringify({ value: [oldMail, newMail] }), { status: 200 }),
    new Response(JSON.stringify({ value: [newMail] }), { status: 200 }),
  ];
  const requests = [];
  let now = Date.parse("2026-07-10T01:30:00Z");
  const poller = new OutlookMailboxPoller({
    outlookSyncFile: stateFile,
    outlookMaxBodyChars: 30000,
  }, {
    getAccessToken: async () => "token",
  }, {
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      return replies.shift();
    },
    now: () => now,
  });
  try {
    const baseline = await poller.poll();
    assert.equal(baseline.initial, true);
    assert.deepEqual(baseline.messages, []);
    now = Date.parse("2026-07-10T02:01:00Z");
    const update = await poller.poll();
    assert.equal(update.messages.length, 1);
    assert.equal(update.messages[0].id, "new");
    assert.match(update.messages[0].text, /第一行\n第二行/);
    const duplicate = await poller.poll();
    assert.deepEqual(duplicate.messages, []);
    assert.match(requests[1].url, /%24filter=/);
    assert.equal(requests[1].options.headers.Prefer, 'outlook.body-content-type="text"');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("Outlook formatter uses preview instead of forwarding raw HTML", () => {
  const text = formatOutlookMessage({
    ...message("html", "2026-07-10T02:00:00Z"),
    body: { contentType: "html", content: "<script>bad()</script><b>secret</b>" },
    bodyPreview: "安全预览",
  });
  assert.match(text, /安全预览/);
  assert.doesNotMatch(text, /<script>/);
});
