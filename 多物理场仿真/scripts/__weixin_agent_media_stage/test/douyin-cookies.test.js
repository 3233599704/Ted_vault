import assert from "node:assert/strict";
import test from "node:test";

import { cookiesToNetscape } from "../src/douyin-cookies.js";

test("Douyin cookie export includes only approved domains", () => {
  const text = cookiesToNetscape([
    { domain: ".douyin.com", path: "/", secure: true, expires: 1234.9, name: "sessionid", value: "abc" },
    { domain: "www.iesdouyin.com", path: "/", secure: false, expires: -1, name: "ttwid", value: "xyz" },
    { domain: ".google.com", path: "/", secure: true, expires: 9999, name: "SID", value: "secret" },
  ]);
  assert.match(text, /\.douyin\.com\tTRUE\t\/\tTRUE\t1234\tsessionid\tabc/);
  assert.match(text, /www\.iesdouyin\.com\tFALSE\t\/\tFALSE\t0\tttwid\txyz/);
  assert.doesNotMatch(text, /google|secret/);
});
