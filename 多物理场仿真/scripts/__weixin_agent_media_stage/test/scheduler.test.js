import assert from "node:assert/strict";
import test from "node:test";

import { StockScheduler, localClock } from "../src/scheduler.js";
import { AgentStorage } from "../src/storage.js";

test("stock scheduler queues at most one job per local date", () => {
  const storage = new AgentStorage(":memory:");
  try {
    storage.saveContext("user-1", "ctx");
    storage.setSchedule("user-1", "stock_daily", {
      enabled: true,
      timeLocal: "15:30",
    });
    const scheduler = new StockScheduler({
      storage,
      log: () => {},
      now: () => new Date(2026, 6, 10, 15, 31),
    });
    scheduler.tick();
    scheduler.tick();
    assert.equal(storage.getQueueStats().pending, 1);
    const job = storage.claimNextJob(["tool"]);
    assert.equal(job.payload.action, "scheduled_daily");
    assert.equal(storage.getSchedule("user-1", "stock_daily").last_run_date, "2026-07-10");
  } finally {
    storage.close();
  }
});

test("local clock is stable and zero padded", () => {
  assert.deepEqual(localClock(new Date(2026, 0, 2, 3, 4)), {
    date: "2026-01-02",
    time: "03:04",
  });
});
