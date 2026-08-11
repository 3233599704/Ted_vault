export function localClock(now = new Date()) {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  const hour = String(now.getHours()).padStart(2, "0");
  const minute = String(now.getMinutes()).padStart(2, "0");
  return { date: `${year}-${month}-${day}`, time: `${hour}:${minute}` };
}

export class StockScheduler {
  constructor({ storage, log, intervalMs = 30_000, now = () => new Date() }) {
    this.storage = storage;
    this.log = log;
    this.intervalMs = intervalMs;
    this.now = now;
    this.running = false;
    this.loopPromise = null;
    this.timer = null;
    this.wake = null;
  }

  start() {
    if (this.running) return;
    this.running = true;
    this.loopPromise = this.loop();
  }

  async stop() {
    this.running = false;
    if (this.timer) clearTimeout(this.timer);
    this.wake?.();
    await this.loopPromise;
  }

  async loop() {
    while (this.running) {
      try { this.tick(); } catch (error) { this.log(`股票定时器异常: ${String(error).slice(0, 500)}`); }
      await new Promise((resolve) => {
        this.wake = resolve;
        this.timer = setTimeout(resolve, this.intervalMs);
      });
      this.timer = null;
      this.wake = null;
    }
  }

  tick() {
    const clock = localClock(this.now());
    for (const schedule of this.storage.listEnabledSchedules("stock_daily")) {
      if (schedule.last_run_date === clock.date || clock.time < schedule.time_local) continue;
      const context = this.storage.getLatestContext(schedule.user_id);
      if (!context?.context_token) continue;
      const id = `schedule:stock_daily:${schedule.user_id}:${clock.date}`;
      const inserted = this.storage.enqueueJob({
        id,
        kind: "tool",
        userId: schedule.user_id,
        sourceKey: id,
        payload: {
          tool: "stock",
          action: "scheduled_daily",
          args: {
            includeHoldings: schedule.settings?.includeHoldings !== false,
            includePicks: schedule.settings?.includePicks !== false,
            includeWatchlist: schedule.settings?.includeWatchlist === true,
            researchThemes: schedule.settings?.researchThemes || [],
          },
          originalText: "每日股票推送",
          contextToken: context.context_token,
        },
      });
      this.storage.markScheduleRun(schedule.user_id, "stock_daily", clock.date);
      if (inserted) this.log(`股票定时任务已入队: ${schedule.user_id.slice(-12)} / ${clock.date}`);
    }
  }
}
