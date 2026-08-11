import assert from "node:assert/strict";
import test from "node:test";

import { AgentStorage } from "../src/storage.js";
import { ReminderTool, parseReminderRequest } from "../src/tools/reminder-tool.js";

const NOW = new Date(2026, 6, 11, 14, 20, 0, 0);

test("reminder parser understands relative and natural Chinese clock times", () => {
  const relative = parseReminderRequest("半小时后提醒我拿快递", NOW);
  assert.equal(relative.dueAt, NOW.getTime() + 30 * 60_000);
  assert.equal(relative.task, "拿快递");

  const evening = parseReminderRequest("晚上八点提醒我写作业", NOW);
  assert.equal(new Date(evening.dueAt).getHours(), 20);
  assert.equal(evening.task, "写作业");

  const tomorrow = parseReminderRequest("明早九点提醒我交实验报告", NOW);
  const tomorrowDate = new Date(tomorrow.dueAt);
  assert.equal(tomorrowDate.getDate(), 12);
  assert.equal(tomorrowDate.getHours(), 9);

  const afternoon = parseReminderRequest("提醒我下午三点半去开会", NOW);
  assert.equal(new Date(afternoon.dueAt).getHours(), 15);
  assert.equal(new Date(afternoon.dueAt).getMinutes(), 30);
  assert.equal(afternoon.task, "去开会");
});

test("past ambiguous times roll to tomorrow but explicit today is rejected", () => {
  const late = new Date(2026, 6, 11, 21, 0, 0, 0);
  const ambiguous = parseReminderRequest("八点提醒我吃药", late);
  assert.equal(new Date(ambiguous.dueAt).getDate(), 12);
  assert.match(parseReminderRequest("今晚八点提醒我吃药", late).error, /已经过去/);
});

test("reminder parser ignores ongoing push and stock monitoring language", () => {
  assert.equal(parseReminderRequest("有新消息记得推送给我", NOW), null);
  assert.equal(
    parseReminderRequest(
      "明天要开盘了，帮我看着点，啥时候你觉得要减仓就跟我说，还有值得买的股票记得推送",
      NOW,
    ),
    null,
  );
  assert.equal(
    parseReminderRequest(
      "以后每天推送时记得带上我持有股票的分析，觉得合适的时候提醒我对持仓做出操作",
      NOW,
    ),
    null,
  );
  assert.match(parseReminderRequest("提醒我写作业", NOW).error, /没看懂时间/);
});

test("reminder tool persists, lists, and cancels delayed outbound jobs", async () => {
  const storage = new AgentStorage(":memory:");
  const tool = new ReminderTool({}, { now: () => NOW });
  try {
    const route = tool.route("晚上八点提醒我写作业");
    assert.equal(route.action, "create");
    assert.match(route.acknowledgement, /20:00/);
    await tool.execute({
      job: {
        id: "tool-reminder-1",
        user_id: "user",
        payload: { action: route.action, args: route.args, contextToken: "ctx-old" },
      },
      storage,
    });
    const reminders = storage.listPendingReminders("user");
    assert.equal(reminders.length, 1);
    assert.equal(reminders[0].payload.refreshContext, true);
    assert.equal(reminders[0].payload.reminder.task, "写作业");

    const list = await tool.execute({
      job: { user_id: "user", payload: { action: "list", args: {} } },
      storage,
    });
    assert.match(list.reply, /写作业/);

    const cancelled = await tool.execute({
      job: { user_id: "user", payload: { action: "cancel_latest", args: {} } },
      storage,
    });
    assert.match(cancelled.reply, /已经取消/);
    assert.equal(storage.listPendingReminders("user").length, 0);
  } finally {
    storage.close();
  }
});

test("reminder outbound refreshes the conversation context at delivery time", async () => {
  const storage = new AgentStorage(":memory:");
  const contexts = [];
  const { JobWorker } = await import("../src/job-worker.js");
  const worker = new JobWorker({
    config: { maxJobAttempts: 3 },
    storage,
    agent: {},
    weixin: {
      sendText: async (_account, _to, _text, context) => contexts.push(context),
    },
    account: {},
    log: () => {},
  });
  try {
    storage.saveContext("user", "ctx-new");
    storage.enqueueJob({
      id: "reminder:delivery",
      kind: "outbound",
      userId: "user",
      payload: {
        to: "user",
        text: "提醒你：写作业",
        contextToken: "ctx-old",
        refreshContext: true,
      },
    });
    await worker.process(storage.claimNextJob(["outbound"]));
    assert.deepEqual(contexts, ["ctx-new"]);
  } finally {
    storage.close();
  }
});
