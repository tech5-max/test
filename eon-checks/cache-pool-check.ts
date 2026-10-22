// Implementation check for two eon load-test switches, run with Bun against the
// REAL service source — no database or Redis needed (pg pools are lazy, and the
// cache stub never connects). Asserts:
//   CACHE_ENABLED=false -> both services' redisClient is the inert stub
//                          (get() returns null, set() resolves, status "disabled")
//   CACHE_ENABLED!=false -> both services' redisClient is a real ioredis client
//   DB_POOL_MAX=N        -> every pool's max is N (timeSeries/portfolio floor 20)
//
// Usage (see run.sh): CACHE_ENABLED=false DB_POOL_MAX=25 bun cache-pool-check.ts

const FD = "../../predictive-ai-web-services/fast-dragon";
const EG = "../../predictive-ai-web-services/express-guardian";

const fails: string[] = [];
const ok = (cond: boolean, msg: string) => {
  console.log(`  ${cond ? "✓" : "✗"} ${msg}`);
  if (!cond) fails.push(msg);
};

const cacheEnabled = (process.env.CACHE_ENABLED ?? "true").trim().toLowerCase() !== "false";
const poolMax = parseInt(process.env.DB_POOL_MAX || "10", 10);
const floor20 = Math.max(poolMax, 20);

console.log(`▶ CACHE_ENABLED=${process.env.CACHE_ENABLED ?? "(unset→true)"} · DB_POOL_MAX=${poolMax}`);

// ---- cache switch ----
const fdRedis = await import(`${FD}/services/redis.ts`);
const egRedis = await import(`${EG}/utils/redis.ts`);

for (const [name, mod] of [["fast-dragon", fdRedis], ["express-guardian", egRedis]] as const) {
  const client: any = mod.redisClient;
  if (!cacheEnabled) {
    ok(client.status === "disabled", `${name}: cache OFF → redisClient is the stub (status "disabled")`);
    ok((await client.get("probe")) === null, `${name}: cache OFF → get() returns null (miss)`);
    const setRes = await client.set("probe", "v", "EX", 10);
    ok(setRes === "OK" || setRes === undefined, `${name}: cache OFF → set() is a resolving no-op`);
  } else {
    ok(client.status !== "disabled", `${name}: cache ON → redisClient is a real ioredis client`);
  }
}

// ---- pool size switch ----
const fdDb: any = await import(`${FD}/db/config.ts`);
const fdPortfolio: any = await import(`${FD}/services/portfolioDB.ts`);
const egDb: any = await import(`${EG}/db/config.ts`);

ok(fdDb.coinDetailsDB.options.max === poolMax, `fast-dragon coinDetailsDB.max === ${poolMax}`);
ok(fdDb.userDetailsDB.options.max === poolMax, `fast-dragon userDetailsDB.max === ${poolMax}`);
ok(fdDb.timeSeriesDB.options.max === floor20, `fast-dragon timeSeriesDB.max === ${floor20} (floor 20)`);
ok(fdPortfolio.portfolioPool.options.max === floor20, `fast-dragon portfolioPool.max === ${floor20} (floor 20)`);
ok(egDb.userDetailsDB.options.max === poolMax, `express-guardian userDetailsDB.max === ${poolMax}`);
ok(egDb.coinDetailsDB.options.max === poolMax, `express-guardian coinDetailsDB.max === ${poolMax}`);

if (fails.length) {
  console.log(`\nFAIL (${fails.length})`);
  process.exit(1);
}
console.log("\nPASS");
process.exit(0);
