// Run with `npm test` (Node's built-in test runner; Node strips the TypeScript types).
import assert from "node:assert/strict";
import { test } from "node:test";

import { compareDecimal, count, percent, ratioPercent, sharePercent, usd } from "./format.ts";

test("usd rounds the decimal string half-up without floating point", () => {
  assert.equal(usd("744.9550000000"), "$744.96");
  assert.equal(usd("0.0050000000"), "$0.0050");
  assert.equal(usd("1234567.8949999999"), "$1,234,567.89");
  assert.equal(usd("0.1950000000"), "$0.20"); // 0.195 as a float would round to 0.19
  assert.equal(usd("-12.3450000000"), "−$12.35");
  assert.equal(usd("0"), "$0.00");
  assert.equal(usd(null), "—");
});

test("usd keeps 4 decimals for sub-cent amounts unless told otherwise", () => {
  assert.equal(usd("0.0004321000"), "$0.0004");
  assert.equal(usd("0.0004321000", 2), "$0.00");
});

test("count groups thousands and drops decimals", () => {
  assert.equal(count("1020838"), "1,020,838");
  assert.equal(count(950), "950");
});

test("percentages", () => {
  assert.equal(percent("-17.53"), "−17.53%");
  assert.equal(ratioPercent("0.0312000000"), "3%");
  assert.equal(ratioPercent("0.0350000000"), "4%");
  assert.equal(ratioPercent("1.2000000000"), "120%");
});

test("sharePercent divides decimal strings exactly and rounds half-up", () => {
  assert.equal(sharePercent("1240.00", "4860.00"), 26);
  assert.equal(sharePercent("1", "8"), 13); // 12.5
  assert.equal(sharePercent("0", "5"), 0);
  assert.equal(sharePercent("5", "5"), 100);
  assert.equal(sharePercent("1", "0"), null);
  assert.equal(sharePercent(null, "5"), null);
});

test("compareDecimal", () => {
  assert.equal(compareDecimal("10.5", "9.99"), 1);
  assert.equal(compareDecimal("-1", "0"), -1);
  assert.equal(compareDecimal("2.50", "2.5"), 0);
});
