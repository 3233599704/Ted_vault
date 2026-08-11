const CHINESE_DIGITS = new Map([
  ["零", 0], ["〇", 0], ["一", 1], ["二", 2], ["两", 2], ["三", 3],
  ["四", 4], ["五", 5], ["六", 6], ["七", 7], ["八", 8], ["九", 9],
]);

function chineseInteger(raw) {
  const text = String(raw || "").trim();
  if (/^\d+(?:\.\d+)?$/.test(text)) return Number(text);
  if (text === "十") return 10;
  if (text.includes("十")) {
    const [left, right] = text.split("十");
    const tens = left ? CHINESE_DIGITS.get(left) : 1;
    const ones = right ? CHINESE_DIGITS.get(right) : 0;
    return tens == null || ones == null ? null : tens * 10 + ones;
  }
  if (text.length === 1) return CHINESE_DIGITS.get(text) ?? null;
  return null;
}

function startOfLocalDay(now) {
  const value = new Date(now);
  value.setHours(0, 0, 0, 0);
  return value;
}

export function formatReminderTime(timestamp) {
  const value = new Date(timestamp);
  const month = value.getMonth() + 1;
  const day = value.getDate();
  const hour = String(value.getHours()).padStart(2, "0");
  const minute = String(value.getMinutes()).padStart(2, "0");
  return `${month}月${day}日 ${hour}:${minute}`;
}

function cleanTask(text, matchedTime) {
  let task = String(text || "");
  if (matchedTime) task = task.replace(matchedTime, " ");
  task = task
    .replace(/(?:请|麻烦)?(?:你)?(?:到点|到时候)?\s*(?:提醒|叫|喊)(?:一下)?(?:我)?/g, " ")
    .replace(/(?:帮我|记得|别忘了)/g, " ")
    .replace(/(?:一下|这件事)$/g, " ")
    .replace(/^[\s，,。.!！：:；;]+|[\s，,。.!！：:；;]+$/g, "")
    .replace(/\s{2,}/g, " ");
  return task;
}

function relativeReminder(text, now) {
  const match = text.match(/(半|\d+(?:\.\d+)?|[零〇一二两三四五六七八九十]{1,3})\s*(?:个)?\s*(分钟|分|小时|钟头)后/);
  if (!match) return null;
  const unit = match[2];
  const amount = match[1] === "半" ? 0.5 : chineseInteger(match[1]);
  if (!Number.isFinite(amount) || amount <= 0) return { error: "提醒时间没有看懂。" };
  const milliseconds = amount * (unit === "分钟" || unit === "分" ? 60_000 : 3_600_000);
  if (milliseconds < 60_000 || milliseconds > 366 * 24 * 3_600_000) {
    return { error: "提醒时间需要在 1 分钟到 366 天之间。" };
  }
  return { dueAt: now.getTime() + milliseconds, matchedTime: match[0] };
}

function exactReminder(text, now) {
  const match = text.match(
    /(今天|明天|后天)?\s*(凌晨|早上|上午|中午|下午|傍晚|晚上|今晚|明早)?\s*(\d{1,2}|[零〇一二两三四五六七八九十]{1,3})\s*(?:(?:[:：]\s*([0-5]?\d))|(?:点|时)\s*(半|一刻|三刻|\d{1,2}|[零〇一二两三四五六七八九十]{1,3})?\s*分?)/,
  );
  if (!match) return null;
  let [, dayWord = "", period = "", rawHour, colonMinute, rawMinute = ""] = match;
  const explicitlyToday = dayWord === "今天" || period === "今晚";
  if (period === "今晚") period = "晚上";
  if (period === "明早") {
    period = "早上";
    dayWord = "明天";
  }
  let hour = chineseInteger(rawHour);
  let minute = 0;
  if (colonMinute) minute = Number(colonMinute);
  else if (rawMinute === "半") minute = 30;
  else if (rawMinute === "一刻") minute = 15;
  else if (rawMinute === "三刻") minute = 45;
  else if (rawMinute) minute = chineseInteger(rawMinute);
  if (!Number.isInteger(hour) || !Number.isInteger(minute) || minute < 0 || minute > 59) {
    return { error: "提醒的具体时间没有看懂。" };
  }
  if (["下午", "傍晚", "晚上"].includes(period) && hour < 12) hour += 12;
  if (period === "中午" && hour < 11) hour += 12;
  if (period === "凌晨" && hour === 12) hour = 0;
  if (hour < 0 || hour > 23) return { error: "提醒的小时需要在 0 到 23 点之间。" };

  const dayOffset = dayWord === "后天" ? 2 : dayWord === "明天" ? 1 : 0;
  const due = startOfLocalDay(now);
  due.setDate(due.getDate() + dayOffset);
  due.setHours(hour, minute, 0, 0);
  const explicitDay = Boolean(dayWord || explicitlyToday);
  if (due.getTime() <= now.getTime()) {
    if (explicitlyToday) return { error: "你说的今天这个时间已经过去了。" };
    if (!explicitDay) due.setDate(due.getDate() + 1);
  }
  return { dueAt: due.getTime(), matchedTime: match[0] };
}

export function parseReminderRequest(text, now = new Date()) {
  const normalized = String(text || "").trim();
  const directIntent = /(?:提醒(?:一下)?我|(?:到点|到时候)?(?:叫|喊)(?:一下)?我)/.test(normalized);
  const reminderVerb = /(?:提醒|叫我|喊我)/.test(normalized);
  if (!reminderVerb) return null;
  const timing = relativeReminder(normalized, now) || exactReminder(normalized, now);
  if (!timing) {
    const ongoingOrConditional = /(?:每天|每日|以后|后续|持续|一旦|如果|觉得|合适|需要|该|推送|股票|持仓|减仓|卖出)/.test(normalized);
    if (ongoingOrConditional) return null;
    return directIntent
      ? { error: "我看到了提醒意图，但没看懂时间。可以说“今晚八点提醒我写作业”。" }
      : null;
  }
  if (timing.error) return timing;
  const task = cleanTask(normalized, timing.matchedTime);
  if (!task) return { error: "时间看懂了，但还不知道到点要提醒你做什么。" };
  return { dueAt: timing.dueAt, task };
}

export class ReminderTool {
  constructor(_config, options = {}) {
    this.name = "reminder";
    this.now = options.now || (() => new Date());
  }

  route(text) {
    const normalized = String(text || "").trim();
    if (/^\/reminder\s+list$/i.test(normalized) || /(?:查看|看看|列出).*(?:我的)?提醒/.test(normalized)) {
      return { action: "list", args: {}, silentAcknowledgement: true };
    }
    if (/^\/reminder\s+cancel\s+all$/i.test(normalized) || /取消(?:我的)?所有提醒/.test(normalized)) {
      return { action: "cancel_all", args: {}, silentAcknowledgement: true };
    }
    if (/^\/reminder\s+cancel$/i.test(normalized) || /(?:取消|删除)(?:刚才|最近|上一个)?(?:的)?提醒/.test(normalized)) {
      return { action: "cancel_latest", args: {}, silentAcknowledgement: true };
    }
    const parsed = parseReminderRequest(normalized, this.now());
    if (!parsed) return null;
    if (parsed.error) {
      return { action: "invalid", args: { error: parsed.error }, acknowledgement: parsed.error };
    }
    return {
      action: "create",
      args: parsed,
      acknowledgement: `好，${formatReminderTime(parsed.dueAt)} 提醒你：${parsed.task}。`,
    };
  }

  async execute({ job, storage }) {
    const action = job.payload.action;
    if (action === "invalid") return { skipped: true, reply: "", model: "reminder:local", usage: {} };
    if (action === "create") {
      const { dueAt, task } = job.payload.args;
      const id = `reminder:${job.id}`;
      storage.enqueueJob({
        id,
        kind: "outbound",
        userId: job.user_id,
        sourceKey: id,
        availableAt: Number(dueAt),
        payload: {
          to: job.user_id,
          contextToken: job.payload.contextToken,
          refreshContext: true,
          text: `提醒你：${task}`,
          reminder: { task, dueAt: Number(dueAt) },
        },
      });
      return { skipped: true, reply: "", model: "reminder:local", usage: {} };
    }
    if (action === "list") {
      const reminders = storage.listPendingReminders(job.user_id);
      const reply = reminders.length
        ? ["你现在有这些提醒：", ...reminders.map((item, index) => (
            `${index + 1}. ${formatReminderTime(item.availableAt)}｜${item.payload.reminder?.task || item.payload.text}`
          ))].join("\n")
        : "现在没有还没到点的提醒。";
      return { reply, model: "reminder:local", usage: {} };
    }
    const all = action === "cancel_all";
    const changed = storage.cancelPendingReminders(job.user_id, { all });
    const reply = changed
      ? all ? `已经取消 ${changed} 个提醒。` : "已经取消最近创建的提醒。"
      : "现在没有可以取消的提醒。";
    return { reply, model: "reminder:local", usage: {} };
  }
}
