import { DatabaseSync } from "node:sqlite";

function parseJson(raw, fallback = {}) {
  try {
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export class AgentStorage {
  constructor(filePath) {
    this.db = new DatabaseSync(filePath);
    this.db.exec("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON;");
    this.migrate();
  }

  migrate() {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
        content TEXT NOT NULL,
        model TEXT,
        created_at INTEGER NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages(user_id, id DESC);

      CREATE TABLE IF NOT EXISTS user_state (
        user_id TEXT PRIMARY KEY,
        selected_model TEXT,
        context_token TEXT,
        context_updated_at INTEGER,
        updated_at INTEGER NOT NULL
      );

      CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        user_id TEXT NOT NULL,
        source_key TEXT UNIQUE,
        payload_json TEXT NOT NULL,
        result_json TEXT,
        status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'done', 'failed', 'cancelled')),
        attempts INTEGER NOT NULL DEFAULT 0,
        available_at INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        last_error TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_jobs_ready ON jobs(status, available_at, created_at);

      CREATE TABLE IF NOT EXISTS inbound_receipts (
        message_key TEXT PRIMARY KEY,
        job_id TEXT NOT NULL,
        created_at INTEGER NOT NULL
      );

      CREATE TABLE IF NOT EXISTS usage_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        model TEXT NOT NULL,
        prompt_tokens INTEGER NOT NULL DEFAULT 0,
        completion_tokens INTEGER NOT NULL DEFAULT 0,
        total_tokens INTEGER NOT NULL DEFAULT 0,
        latency_ms INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_usage_user_time ON usage_events(user_id, created_at);

      CREATE TABLE IF NOT EXISTS schedules (
        user_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 0,
        time_local TEXT NOT NULL,
        last_run_date TEXT,
        settings_json TEXT NOT NULL DEFAULT '{}',
        updated_at INTEGER NOT NULL,
        PRIMARY KEY(user_id, kind)
      );
    `);
  }

  transaction(fn) {
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const result = fn();
      this.db.exec("COMMIT");
      return result;
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }

  saveContext(userId, contextToken) {
    if (!userId || !contextToken) return;
    const now = Date.now();
    this.db.prepare(`
      INSERT INTO user_state(user_id, context_token, context_updated_at, updated_at)
      VALUES (?, ?, ?, ?)
      ON CONFLICT(user_id) DO UPDATE SET
        context_token=excluded.context_token,
        context_updated_at=excluded.context_updated_at,
        updated_at=excluded.updated_at
    `).run(userId, contextToken, now, now);
  }

  getLatestContext(userId = "") {
    const row = userId
      ? this.db.prepare(`
          SELECT user_id, context_token, context_updated_at
          FROM user_state WHERE user_id=? AND context_token IS NOT NULL
        `).get(userId)
      : this.db.prepare(`
          SELECT user_id, context_token, context_updated_at
          FROM user_state WHERE context_token IS NOT NULL
          ORDER BY context_updated_at DESC LIMIT 1
        `).get();
    return row || null;
  }

  setUserModel(userId, model) {
    const now = Date.now();
    this.db.prepare(`
      INSERT INTO user_state(user_id, selected_model, updated_at)
      VALUES (?, ?, ?)
      ON CONFLICT(user_id) DO UPDATE SET
        selected_model=excluded.selected_model,
        updated_at=excluded.updated_at
    `).run(userId, model, now);
  }

  getUserModel(userId) {
    return this.db.prepare(
      "SELECT selected_model FROM user_state WHERE user_id=?",
    ).get(userId)?.selected_model || "";
  }

  enqueueJob({ id, kind, userId, sourceKey = null, payload, availableAt = Date.now() }) {
    const now = Date.now();
    const result = this.db.prepare(`
      INSERT OR IGNORE INTO jobs(
        id, kind, user_id, source_key, payload_json, status,
        attempts, available_at, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)
    `).run(id, kind, userId, sourceKey, JSON.stringify(payload), availableAt, now, now);
    return Number(result.changes) === 1;
  }

  enqueueInboundBurst({
    id,
    messageKey,
    userId,
    payload,
    quietMs,
    maxWaitMs,
    maxMessages,
    notBefore = 0,
    now = Date.now(),
  }) {
    return this.transaction(() => {
      const duplicate = this.db.prepare(
        "SELECT job_id FROM inbound_receipts WHERE message_key=?",
      ).get(messageKey);
      if (duplicate) return { inserted: false, duplicate: true, jobId: duplicate.job_id };

      const candidates = this.db.prepare(`
        SELECT id, payload_json, created_at
        FROM jobs
        WHERE kind='inbound' AND user_id=? AND status='pending'
          AND attempts=0 AND result_json IS NULL
        ORDER BY created_at DESC LIMIT 4
      `).all(userId);
      let target = null;
      let targetPayload = null;
      for (const candidate of candidates) {
        const parsed = parseJson(candidate.payload_json, null);
        if (!parsed?.burstFirstAt || !Array.isArray(parsed.messageKeys)) continue;
        if (parsed.messageKeys.length >= maxMessages) continue;
        if (now > Number(parsed.burstFirstAt) + maxWaitMs) continue;
        target = candidate;
        targetPayload = parsed;
        break;
      }

      const receipt = this.db.prepare(`
        INSERT INTO inbound_receipts(message_key, job_id, created_at)
        VALUES (?, ?, ?)
      `);
      if (target) {
        const texts = [targetPayload.text, payload.text]
          .map((item) => String(item || "").trim())
          .filter(Boolean);
        const messageKeys = [...targetPayload.messageKeys, messageKey];
        const merged = {
          ...targetPayload,
          text: texts.join("\n"),
          media: [...(targetPayload.media || []), ...(payload.media || [])],
          contextToken: payload.contextToken || targetPayload.contextToken || "",
          receivedAt: payload.receivedAt || now,
          messageKeys,
          messageCount: messageKeys.length,
        };
        const deadline = Number(targetPayload.burstFirstAt) + maxWaitMs;
        const flushNow = messageKeys.length >= maxMessages || /^(?:好了|好啦|就这些|说完了|发完了)$/u.test(String(payload.text || "").trim());
        const availableAt = Math.max(
          notBefore,
          flushNow ? now : Math.min(now + quietMs, deadline),
        );
        this.db.prepare(`
          UPDATE jobs SET payload_json=?, available_at=?, updated_at=?
          WHERE id=? AND status='pending' AND attempts=0
        `).run(JSON.stringify(merged), availableAt, now, target.id);
        receipt.run(messageKey, target.id, now);
        return {
          inserted: true,
          merged: true,
          duplicate: false,
          jobId: target.id,
          messageCount: messageKeys.length,
          availableAt,
        };
      }

      const burstPayload = {
        ...payload,
        burstFirstAt: now,
        messageKeys: [messageKey],
        messageCount: 1,
      };
      const flushNow = /^(?:好了|好啦|就这些|说完了|发完了)$/u.test(String(payload.text || "").trim());
      const availableAt = Math.max(notBefore, flushNow ? now : now + quietMs);
      this.db.prepare(`
        INSERT INTO jobs(
          id, kind, user_id, source_key, payload_json, status,
          attempts, available_at, created_at, updated_at
        ) VALUES (?, 'inbound', ?, ?, ?, 'pending', 0, ?, ?, ?)
      `).run(id, userId, messageKey, JSON.stringify(burstPayload), availableAt, now, now);
      receipt.run(messageKey, id, now);
      return {
        inserted: true,
        merged: false,
        duplicate: false,
        jobId: id,
        messageCount: 1,
        availableAt,
      };
    });
  }

  recoverStaleJobs(staleMs = 5 * 60_000) {
    const cutoff = Date.now() - staleMs;
    return this.db.prepare(`
      UPDATE jobs SET status='pending', available_at=?, updated_at=?,
        last_error='Recovered after interrupted process'
      WHERE status='running' AND updated_at < ?
    `).run(Date.now(), Date.now(), cutoff).changes;
  }

  claimNextJob(kinds = []) {
    return this.transaction(() => {
      const allowedKinds = Array.isArray(kinds) ? kinds.filter(Boolean) : [];
      const kindClause = allowedKinds.length
        ? ` AND kind IN (${allowedKinds.map(() => "?").join(",")})`
        : "";
      const row = this.db.prepare(`
        SELECT * FROM jobs
        WHERE status='pending' AND available_at <= ?${kindClause}
        ORDER BY available_at, created_at LIMIT 1
      `).get(Date.now(), ...allowedKinds);
      if (!row) return null;
      this.db.prepare(`
        UPDATE jobs SET status='running', attempts=attempts+1, updated_at=?
        WHERE id=? AND status='pending'
      `).run(Date.now(), row.id);
      return {
        ...row,
        attempts: Number(row.attempts) + 1,
        payload: parseJson(row.payload_json),
        result: row.result_json ? parseJson(row.result_json) : null,
      };
    });
  }

  saveJobResult(id, result) {
    this.db.prepare(
      "UPDATE jobs SET result_json=?, updated_at=? WHERE id=?",
    ).run(JSON.stringify(result), Date.now(), id);
  }

  saveJobCheckpointWithUsage(id, result, userId, usageEvents = []) {
    this.transaction(() => {
      this.db.prepare(
        "UPDATE jobs SET result_json=?, updated_at=? WHERE id=?",
      ).run(JSON.stringify(result), Date.now(), id);
      const insert = this.db.prepare(`
        INSERT INTO usage_events(
          user_id, model, prompt_tokens, completion_tokens,
          total_tokens, latency_ms, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
      `);
      const now = Date.now();
      for (const event of usageEvents) {
        insert.run(
          userId,
          event.model || "media",
          Number(event.usage?.prompt_tokens || 0),
          Number(event.usage?.completion_tokens || 0),
          Number(event.usage?.total_tokens || 0),
          Number(event.latencyMs || 0),
          now,
        );
      }
    });
  }

  saveToolResultAndTurn(id, result, turn) {
    this.transaction(() => {
      const updated = this.db.prepare(`
        UPDATE jobs SET result_json=?, updated_at=?
        WHERE id=? AND result_json IS NULL
      `).run(JSON.stringify(result), Date.now(), id);
      if (!updated.changes || !turn?.assistantText) return;
      const now = Date.now();
      const insert = this.db.prepare(`
        INSERT INTO messages(user_id, role, content, model, created_at)
        VALUES (?, ?, ?, ?, ?)
      `);
      insert.run(turn.userId, "user", turn.userText, turn.model, now);
      insert.run(turn.userId, "assistant", turn.assistantText, turn.model, now + 1);
      this.db.prepare(`
        INSERT INTO usage_events(
          user_id, model, prompt_tokens, completion_tokens,
          total_tokens, latency_ms, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
      `).run(
        turn.userId,
        turn.model,
        Number(turn.usage?.prompt_tokens || 0),
        Number(turn.usage?.completion_tokens || 0),
        Number(turn.usage?.total_tokens || 0),
        Number(turn.latencyMs || 0),
        now,
      );
    });
  }

  completeJob(id) {
    this.db.prepare(
      "UPDATE jobs SET status='done', updated_at=?, last_error=NULL WHERE id=?",
    ).run(Date.now(), id);
  }

  retryJob(id, error, delayMs) {
    const message = String(error || "Unknown error").slice(0, 1000);
    this.db.prepare(`
      UPDATE jobs SET status='pending', available_at=?, updated_at=?, last_error=?
      WHERE id=?
    `).run(Date.now() + delayMs, Date.now(), message, id);
  }

  failJob(id, error) {
    const message = String(error || "Unknown error").slice(0, 1000);
    this.db.prepare(`
      UPDATE jobs SET status='failed', updated_at=?, last_error=? WHERE id=?
    `).run(Date.now(), message, id);
  }

  getQueueStats() {
    const rows = this.db.prepare(
      "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status",
    ).all();
    return Object.fromEntries(rows.map((row) => [row.status, Number(row.count)]));
  }

  listPendingReminders(userId, limit = 20) {
    return this.db.prepare(`
      SELECT id, payload_json, available_at, created_at
      FROM jobs
      WHERE user_id=? AND kind='outbound' AND status='pending'
        AND id LIKE 'reminder:%'
      ORDER BY available_at ASC LIMIT ?
    `).all(userId, limit).map((row) => ({
      id: row.id,
      payload: parseJson(row.payload_json),
      availableAt: Number(row.available_at),
      createdAt: Number(row.created_at),
    }));
  }

  cancelPendingReminders(userId, { all = false } = {}) {
    const reminders = this.db.prepare(`
      SELECT id FROM jobs
      WHERE user_id=? AND kind='outbound' AND status='pending'
        AND id LIKE 'reminder:%'
      ORDER BY created_at DESC
    `).all(userId);
    const selected = all ? reminders : reminders.slice(0, 1);
    if (!selected.length) return 0;
    const update = this.db.prepare(`
      UPDATE jobs SET status='cancelled', updated_at=?
      WHERE id=? AND status='pending'
    `);
    let changed = 0;
    this.transaction(() => {
      for (const row of selected) changed += Number(update.run(Date.now(), row.id).changes);
    });
    return changed;
  }

  getHistory(userId, limit = 24, maxChars = 24_000) {
    const rows = this.db.prepare(`
      SELECT role, content FROM messages
      WHERE user_id=? ORDER BY id DESC LIMIT ?
    `).all(userId, limit);
    const result = [];
    let chars = 0;
    for (const row of rows) {
      if (chars + row.content.length > maxChars && result.length > 0) break;
      result.push({ role: row.role, content: row.content });
      chars += row.content.length;
    }
    return result.reverse();
  }

  appendTurn(userId, userText, assistantText, model, usage = {}, latencyMs = 0) {
    const now = Date.now();
    this.transaction(() => {
      const insert = this.db.prepare(`
        INSERT INTO messages(user_id, role, content, model, created_at)
        VALUES (?, ?, ?, ?, ?)
      `);
      insert.run(userId, "user", userText, model, now);
      insert.run(userId, "assistant", assistantText, model, now + 1);
      this.db.prepare(`
        INSERT INTO usage_events(
          user_id, model, prompt_tokens, completion_tokens,
          total_tokens, latency_ms, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
      `).run(
        userId,
        model,
        Number(usage.prompt_tokens || 0),
        Number(usage.completion_tokens || 0),
        Number(usage.total_tokens || 0),
        Number(latencyMs || 0),
        now,
      );
    });
  }

  resetConversation(userId) {
    return this.db.prepare("DELETE FROM messages WHERE user_id=?").run(userId).changes;
  }

  getUsageSummary(userId, since = 0) {
    const row = this.db.prepare(`
      SELECT COUNT(*) AS requests,
        COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
        COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
        COALESCE(SUM(total_tokens), 0) AS total_tokens,
        COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
      FROM usage_events WHERE user_id=? AND created_at>=?
    `).get(userId, since);
    return Object.fromEntries(
      Object.entries(row).map(([key, value]) => [key, Number(value)]),
    );
  }

  setSchedule(userId, kind, { enabled, timeLocal, settings = {} }) {
    const now = Date.now();
    this.db.prepare(`
      INSERT INTO schedules(
        user_id, kind, enabled, time_local, settings_json, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(user_id, kind) DO UPDATE SET
        enabled=excluded.enabled,
        time_local=excluded.time_local,
        settings_json=excluded.settings_json,
        updated_at=excluded.updated_at
    `).run(
      userId,
      kind,
      enabled ? 1 : 0,
      timeLocal,
      JSON.stringify(settings),
      now,
    );
    return this.getSchedule(userId, kind);
  }

  getSchedule(userId, kind) {
    const row = this.db.prepare(
      "SELECT * FROM schedules WHERE user_id=? AND kind=?",
    ).get(userId, kind);
    return row ? { ...row, enabled: Boolean(row.enabled), settings: parseJson(row.settings_json) } : null;
  }

  listEnabledSchedules(kind) {
    return this.db.prepare(
      "SELECT * FROM schedules WHERE kind=? AND enabled=1",
    ).all(kind).map((row) => ({
      ...row,
      enabled: true,
      settings: parseJson(row.settings_json),
    }));
  }

  markScheduleRun(userId, kind, dateText) {
    return this.db.prepare(`
      UPDATE schedules SET last_run_date=?, updated_at=?
      WHERE user_id=? AND kind=?
    `).run(dateText, Date.now(), userId, kind).changes;
  }

  close() {
    this.db.close();
  }
}
