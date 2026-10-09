/**
 * Verify the running frontend can actually reach the running backend.
 *
 * Calls exactly the same endpoints the pages call, from Node, using the same
 * base URL the browser bundle was built with (NEXT_PUBLIC_API_BASE_URL).
 *
 * Usage: node ops/check_frontend_api.mjs [apiBaseUrl]
 */

const API = (process.argv[2] ?? "http://127.0.0.1:8000") + "/api/v1";

const calls = [
  ["首页 · 数据集版本", "GET", "/meta/dataset"],
  ["首页 · 招生周期（政策上限）", "GET", "/meta/admission-cycles"],
  ["成绩页 · 分数换算位次", "POST", "/catalog/score-to-rank", { cycle_id: null, score: 590 }],
  ["编辑器 · 推荐条目（无会话时应 404）", "GET", "/recommendations/999999/items"],
];

let failures = 0;

for (const [label, method, path, body] of calls) {
  const options = { method, headers: {} };
  if (body) {
    const payload = { ...body };
    if (payload.cycle_id === null) {
      const cycles = await (await fetch(`${API}/meta/admission-cycles`)).json();
      payload.cycle_id = cycles[0].id;
    }
    options.body = JSON.stringify(payload);
    options.headers["Content-Type"] = "application/json";
  }
  try {
    const response = await fetch(`${API}${path}`, options);
    const text = await response.text();
    const ok = response.ok || response.status === 404;
    console.log(`[${ok ? "PASS" : "FAIL"}] ${label} -> HTTP ${response.status} ${text.slice(0, 110)}`);
    if (!ok) failures += 1;
  } catch (error) {
    console.log(`[FAIL] ${label} -> ${error.message}`);
    failures += 1;
  }
}

// The preference/recommendation creation path the 偏好画像 page uses.
const cycles = await (await fetch(`${API}/meta/admission-cycles`)).json();
const profileResponse = await fetch(`${API}/profiles`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    cycle_id: cycles[0].id,
    display_name: "前端联通性检查",
    score: 590,
    rank: 60000,
    subjects: ["PHY", "CHM", "BIO"],
    foreign_language: "ENG",
  }),
});
const profile = await profileResponse.json();
console.log(
  `[${profileResponse.ok ? "PASS" : "FAIL"}] 成绩页 · 创建档案 -> HTTP ${profileResponse.status} id=${profile.id}`,
);
if (!profileResponse.ok) failures += 1;

const preferenceResponse = await fetch(`${API}/profiles/${profile.id}/preferences`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ risk_mode: "BALANCED", max_tuition: 9000 }),
});
const preference = await preferenceResponse.json();
console.log(
  `[${preferenceResponse.ok ? "PASS" : "FAIL"}] 偏好页 · 创建偏好 -> HTTP ${preferenceResponse.status} v=${preference.version} target=${preference.target_count}`,
);
if (!preferenceResponse.ok) failures += 1;

const runResponse = await fetch(`${API}/recommendations`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ profile_id: profile.id, preference_id: preference.id }),
});
const run = await runResponse.json();
console.log(
  `[${runResponse.ok ? "PASS" : "FAIL"}] 偏好页 · 生成推荐 -> HTTP ${runResponse.status} session=${run.session?.id} pool=${run.pool_size}`,
);
if (!runResponse.ok) failures += 1;

const itemsResponse = await fetch(`${API}/recommendations/${run.session.id}/items?limit=200`);
const items = await itemsResponse.json();
console.log(
  `[${itemsResponse.ok ? "PASS" : "FAIL"}] 推荐页 · 候选卡片 -> HTTP ${itemsResponse.status} total=${items.total}`,
);
if (!itemsResponse.ok) failures += 1;

const schemeResponse = await fetch(`${API}/schemes`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ recommendation_session_id: run.session.id }),
});
const scheme = await schemeResponse.json();
console.log(
  `[${schemeResponse.ok ? "PASS" : "FAIL"}] 推荐页 · 生成方案 -> HTTP ${schemeResponse.status} scheme=${scheme.id} items=${scheme.item_count}`,
);
if (!schemeResponse.ok) failures += 1;

const healthResponse = await fetch(`${API}/schemes/${scheme.id}/health-check`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({}),
});
const health = await healthResponse.json();
console.log(
  `[${healthResponse.ok ? "PASS" : "FAIL"}] 体检页 · 运行体检 -> HTTP ${healthResponse.status} score=${health.score} checks=${health.checks?.length}`,
);
if (!healthResponse.ok) failures += 1;

console.log(`\n演示数据: profile=${profile.id} preference=${preference.id} session=${run.session?.id} scheme=${scheme.id}`);
console.log(failures === 0 ? "前端 ↔ 后端联通性：全部通过" : `失败 ${failures} 项`);
process.exit(failures === 0 ? 0 : 1);
