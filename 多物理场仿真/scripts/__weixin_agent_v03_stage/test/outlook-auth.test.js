import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { OutlookAuthClient } from "../src/outlook-auth-client.js";

function response(payload, status = 200) {
  return new Response(JSON.stringify(payload), { status });
}

const protect = async (text) => Buffer.from(text, "utf8").toString("base64");
const unprotect = async (text) => Buffer.from(text, "base64").toString("utf8");

test("Outlook device login stores an encrypted refresh-token record", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vera-outlook-auth-"));
  const tokenFile = path.join(dir, "token.dpapi");
  const username = "student@example.edu";
  const idToken = `x.${Buffer.from(JSON.stringify({ preferred_username: username, tid: "tenant" })).toString("base64url")}.x`;
  const replies = [
    response({
      device_code: "device-code",
      user_code: "ABCD-EFGH",
      verification_uri: "https://microsoft.com/devicelogin",
      expires_in: 900,
      interval: 1,
    }),
    response({ error: "authorization_pending" }, 400),
    response({
      access_token: "access-token",
      refresh_token: "refresh-token",
      expires_in: 3600,
      scope: "Mail.Read",
      id_token: idToken,
    }),
  ];
  const client = new OutlookAuthClient({
    outlookClientId: "client",
    outlookTenantId: "tenant",
    outlookMailbox: username,
    outlookTokenFile: tokenFile,
  }, {
    fetchImpl: async () => replies.shift(),
    sleep: async () => {},
    protect,
    unprotect,
  });
  let shownCode;
  try {
    const record = await client.deviceLogin((code) => { shownCode = code; });
    assert.equal(shownCode.userCode, "ABCD-EFGH");
    assert.equal(record.username, username);
    assert.equal(await client.getAccessToken(), "access-token");
    assert.equal(fs.readFileSync(tokenFile, "utf8").includes("refresh-token"), false);
    assert.equal((await client.loadRecord()).refreshToken, "refresh-token");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("Outlook token refresh rotates and persists the refresh token", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vera-outlook-refresh-"));
  const tokenFile = path.join(dir, "token.dpapi");
  const client = new OutlookAuthClient({
    outlookClientId: "client",
    outlookTenantId: "tenant",
    outlookMailbox: "student@example.edu",
    outlookTokenFile: tokenFile,
  }, {
    fetchImpl: async () => response({
      access_token: "new-access",
      refresh_token: "new-refresh",
      expires_in: 3600,
    }),
    protect,
    unprotect,
  });
  try {
    await client.saveRecord({ refreshToken: "old-refresh", username: "student@example.edu" });
    assert.equal(await client.getAccessToken(), "new-access");
    assert.equal((await client.loadRecord()).refreshToken, "new-refresh");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
