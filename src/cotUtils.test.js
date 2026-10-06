import fs from "fs";
import path from "path";
import { buildSeries, rangeAt, cotIndex, crowdedFlag, summarize, fmtNet, fmtChg, fmtDate, addDays, niceMax, buildCotBrief, idxTone, basisLabel, idxToneNeutral, groupsFor } from "./cotUtils";

test("fmtNet puts negatives in parentheses", () => {
  expect(fmtNet(56150)).toBe("56,150");
  expect(fmtNet(-65763)).toBe("(65,763)");
  expect(fmtNet(null)).toBe("—");
});

test("fmtChg signs changes", () => {
  expect(fmtChg(1200)).toBe("+1,200");
  expect(fmtChg(-1200)).toBe("−1,200");
  expect(fmtChg(0)).toBe("0");
});

test("date helpers are timezone-safe", () => {
  expect(fmtDate("2026-09-22")).toBe("9/22/2026");
  expect(addDays("2026-09-22", 10)).toBe("2026-10-02");
  expect(addDays("2026-12-28", 7)).toBe("2027-01-04");
});

test("rangeAt uses a trailing inclusive window", () => {
  const v = [5, 1, 9, 3, 4];
  expect(rangeAt(v, 4, 3)).toEqual([3, 9]);
  expect(rangeAt(v, 4, 99)).toEqual([1, 9]);
});

test("cotIndex is 0..100 within the window and null when history is short or flat", () => {
  const up = Array.from({ length: 60 }, (_, k) => k);
  expect(cotIndex(up, 59, 52)).toBe(100);
  const dn = Array.from({ length: 60 }, (_, k) => -k);
  expect(cotIndex(dn, 59, 52)).toBe(0);
  expect(cotIndex([1, 2, 3], 2, 52)).toBeNull();
  expect(cotIndex(new Array(60).fill(7), 59, 52)).toBeNull();
});

test("crowdedFlag thresholds", () => {
  expect(crowdedFlag(95)).toBe("long");
  expect(crowdedFlag(5)).toBe("short");
  expect(crowdedFlag(50)).toBeNull();
  expect(crowdedFlag(null)).toBeNull();
});

test("niceMax gives round symmetric axis maxima", () => {
  expect(niceMax(56150)).toBe(60000);
  expect(niceMax(61000)).toBe(70000);
  expect(niceMax(0)).toBe(1);
});

// Cross-check against the real CFTC pull when it exists: matches the reference
// screenshots (NQ 9/22/2026: Large Specs 56,150, Commercials (65,763), Small Specs 9,613).
const dataFile = path.join(__dirname, "..", "public", "cot_data.json");
const maybe = fs.existsSync(dataFile) ? test : test.skip;
maybe("real data: NQ latest nets and index bounds", () => {
  const d = JSON.parse(fs.readFileSync(dataFile, "utf8"));
  const nq = d.markets.find(m => m.symbol === "NQ");
  const s = buildSeries(nq.rows);
  const sum = summarize(nq);
  if (sum.date === "2026-09-22") {
    expect(sum.ls.net).toBe(56150);
    expect(sum.c.net).toBe(-65763);
    expect(sum.ss.net).toBe(9613);
  }
  for (const m of d.markets) {
    const x = summarize(m);
    for (const g of ["ls", "c", "ss"]) {
      for (const k of ["idx3", "idx1"]) {
        if (x[g][k] != null) { expect(x[g][k]).toBeGreaterThanOrEqual(0); expect(x[g][k]).toBeLessThanOrEqual(100); }
      }
    }
    expect(m.rows.every((r, i) => i === 0 || r[0] > m.rows[i - 1][0])).toBe(true);
  }
  expect(s.dates.length).toBeGreaterThan(200);
});

test("idxTone highlights crowded extremes", () => {
  expect(idxTone(95)).toContain("amber");
  expect(idxTone(5)).toContain("teal");
  expect(idxTone(50)).toBe("text-zinc-400");
  expect(idxTone(null)).toBe("text-zinc-600");
});

maybe("buildCotBrief produces a compact, serialisable brief", () => {
  const d = JSON.parse(fs.readFileSync(dataFile, "utf8"));
  const b = buildCotBrief(d, "2026-10-02");
  // Expectations come from the data itself: cot_data.json is refreshed weekly by the nightly job, so a hard-coded
  // report date or net position goes stale every Friday.
  const utc = (iso) => { const [y, m, dd] = iso.split("-").map(Number); return Date.UTC(y, m - 1, dd); };
  expect(b.prompt.data_age_days).toBe(Math.round((utc("2026-10-02") - utc(d.report_date)) / 86400000));
  expect(b.prompt.key_markets.length).toBeGreaterThanOrEqual(10);
  const nq = b.prompt.key_markets.find(m => m.market.endsWith("(NQ)"));
  const nqLast = d.markets.find(m => m.symbol === "NQ").rows.at(-1);
  expect(nq.large_specs_net).toBe(nqLast[2] - nqLast[3]);
  expect(b.longs.every(s => s.ls.idx3 >= 90)).toBe(true);
  expect(b.shorts.every(s => s.ls.idx3 <= 10)).toBe(true);
  expect(JSON.stringify(b.prompt).length).toBeLessThan(6000);
});

test("basisLabel describes the size of the leveraged-fund position, never a direction call", () => {
  expect(basisLabel(-1350740, 5)).toBe("LEV FUNDS SHORT NEAR 3Y LARGEST");
  expect(basisLabel(-1350740, 95)).toBe("LEV FUNDS SHORT NEAR 3Y SMALLEST");
  expect(basisLabel(5000, 95)).toBe("LEV FUNDS LONG NEAR 3Y LARGEST");
  expect(basisLabel(-1, 50)).toBeNull();
  expect(basisLabel(-1, null)).toBeNull();
  expect(idxToneNeutral(95)).toContain("sky");
  expect(idxToneNeutral(50)).toBe("text-zinc-400");
});

test("groupsFor picks trader-group names by report", () => {
  expect(groupsFor({ report: "legacy" }).map(g => g.label)).toEqual(["Large Specs", "Commercials", "Small Specs"]);
  expect(groupsFor({ report: "tff" }).map(g => g.label)).toEqual(["Asset Managers", "Leveraged Funds", "Dealers"]);
  expect(groupsFor(undefined)[0].label).toBe("Large Specs");
});

maybe("Treasuries come from TFF: asset managers drive crowding, leveraged funds drive the basis label", () => {
  const d = JSON.parse(fs.readFileSync(dataFile, "utf8"));
  const by = Object.fromEntries(d.markets.map(m => [m.symbol, summarize(m)]));
  for (const sym of ["ZT", "ZN", "UB"]) {
    const m = d.markets.find(x => x.symbol === sym);
    expect(m.report).toBe("tff");
    expect(by[sym].tff).toBe(true);
    // slot 1 = asset managers (real money, net long), slot 2 = leveraged funds (basis shorts, net short)
    expect(by[sym].ls.net).toBeGreaterThan(0);
    expect(by[sym].c.net).toBeLessThan(0);
    // crowded flag comes from the asset-manager index, basis label from the leveraged-fund index
    expect(by[sym].flag).toBe(by[sym].ls.idx3 >= 90 ? "long" : by[sym].ls.idx3 <= 10 ? "short" : null);
    expect(by[sym].basisLabel).toBe(basisLabel(by[sym].c.net, by[sym].c.idx3));
  }
  for (const sym of ["NQ", "ES", "GC", "CL", "BTC"]) {
    expect(by[sym].tff).toBe(false);
    expect(by[sym].basisLabel).toBeNull();
  }
  // TFF OI matches the Legacy report's for ZT on the same date: 4,539,374 on 2026-09-22
  const zt = d.markets.find(x => x.symbol === "ZT");
  if (zt.rows[zt.rows.length - 1][0] === "2026-09-22") expect(zt.rows[zt.rows.length - 1][1]).toBe(4539374);
});

maybe("brief payload: bonds use TFF field names and report basis-trade extremes separately", () => {
  const d = JSON.parse(fs.readFileSync(dataFile, "utf8"));
  const b = buildCotBrief(d, "2026-10-02");
  const zt = b.prompt.key_markets.find(m => m.market.endsWith("(ZT)"));
  expect(zt.report).toMatch(/TFF/);
  expect(zt.asset_managers_side).toBe("net long");
  expect(zt).toHaveProperty("leveraged_funds_net");
  expect(zt).not.toHaveProperty("large_specs_net");
  const nq = b.prompt.key_markets.find(m => m.market.endsWith("(NQ)"));
  expect(nq).toHaveProperty("large_specs_net");
  expect(nq).not.toHaveProperty("asset_managers_net");
  // basis extremes always talk about leveraged funds, never "crowded"
  for (const t of b.prompt.treasury_basis_trade_extremes) {
    expect(t).toMatch(/leveraged funds/);
    expect(t).not.toMatch(/crowded/i);
    // meaning is spelled out: SMALLEST = being cut back, LARGEST = big trade / unwind risk (never inverted)
    if (/3Y SMALLEST/.test(t)) expect(t).toMatch(/cut back/);
    if (/3Y LARGEST/.test(t)) expect(t).toMatch(/unwind/);
  }
  expect(JSON.stringify(b.prompt).length).toBeLessThan(7000);
});
