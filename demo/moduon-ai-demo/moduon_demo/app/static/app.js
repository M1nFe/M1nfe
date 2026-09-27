"use strict";
// 모두온 AI 콘솔 — 화면 스크립트. 모든 글자는 textContent로 넣는다(AI 응답·파일 내용을 HTML로 해석하지 않음).

const $ = (sel) => document.querySelector(sel);
let STATE = null;
let PAGE = "collect";
let SELECTED = null;          // 전산 수집 화면에서 펼쳐 본 자료
let POLL = null;
let LAST_ASK = null;          // 자연어 조회 마지막 결과(화면을 다시 그려도 남도록)

// ───────────────────────── DOM 도우미
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const won = (v) => (v === null || v === undefined ? "-" : Number(v).toLocaleString("ko-KR"));
const gae = (v) => (v === null || v === undefined ? "" : `${(v / 10000).toLocaleString("ko-KR")}개`);
const ACTOR = { "AI": "🤖", "코드": "⚙️", "사람": "🙋", "보안": "🛡️", "정답": "📏", "오류": "❗", "DB": "🗄️" };

function table(t) {
  const wrap = h("div", { class: "scroll" });
  wrap.append(h("table", {},
    h("thead", {}, h("tr", {}, t.columns.map((c) => h("th", {}, c)))),
    h("tbody", {}, t.rows.map((r) => h("tr", {}, r.map((v) => h("td", {}, v)))))));
  return h("div", {}, h("h3", {}, t.title), wrap, t.note ? h("p", { class: "muted small" }, t.note) : null);
}

// ───────────────────────── 서버 호출
async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json", "X-Moduon": "1" }, body: JSON.stringify(body),
  };
  const r = await fetch(path, opt);
  const ct = r.headers.get("Content-Type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(data.error || data || `오류 ${r.status}`);
  return data;
}

function toast(msg, err) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (err ? " err" : "");
  t.hidden = false;
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.hidden = true), err ? 6000 : 2600);
}

async function act(fn) {
  try { return await fn(); } catch (e) { toast(e.message, true); }
}

// 오래 걸리는 작업: 진행 상황을 읽어 오른쪽 아래에 보여 준다
async function runJob(path, body, after) {
  const res = await act(() => api(path, body || {}));
  if (!res || !res.job_id) return;
  const box = $("#job"), log = $("#job-log");
  box.hidden = false; box.classList.remove("error"); $("#job-close").hidden = true;
  log.replaceChildren();
  clearInterval(POLL);
  POLL = setInterval(async () => {
    let j;
    try { j = await api(`/api/job/${res.job_id}`); } catch (e) { return; }
    $("#job-title").textContent = j.title + (j.status === "running" ? " — 진행 중" : "");
    log.replaceChildren(...j.messages.slice(-12).map((m) => h("li", {}, m)));
    if (j.status === "running") return;
    clearInterval(POLL);
    if (j.status === "error") {
      box.classList.add("error");
      $("#job-title").textContent = j.title + " — 실패";
      log.append(h("li", { style: "color:var(--bad)" }, j.error));
      $("#job-close").hidden = false;
    } else {
      $("#job-title").textContent = `${j.title} — 완료 (${j.elapsed}초)`;
      setTimeout(() => (box.hidden = true), 1500);
      if (after) after(j.result, res);
    }
    await refresh();
  }, 700);
}

// ───────────────────────── 공통: 상태·엔진·탐색
async function refresh() {
  STATE = await api("/api/state");
  const t = STATE.todo;
  $("#b-collect").textContent = t.mapping || "";
  $("#b-match").textContent = t.matching || "";
  $("#b-review").textContent = (t.review + t.to_promote) || "";
  const e = STATE.engine;
  $("#engine-dot").className = "dot " + (e.mode === "live" ? "live" : "mock");
  $("#engine-dot").title = e.label;
  buildEngineSelect();
  render();
}

function buildEngineSelect() {
  const sel = $("#engine-select"), e = STATE.engine, eng = STATE.engines;
  const cur = `${e.mode === "mock" ? "mock" : e.provider}|${e.mode === "mock" ? "" : e.model}`;
  const opts = [];
  if (eng.ollama.reachable) {
    for (const m of eng.ollama.models) opts.push([`ollama|${m}`, `Ollama 로컬 · ${m} (무료)`]);
    if (!eng.ollama.models.length) opts.push(["", `Ollama 켜짐 · 받은 모델 없음 (ollama pull ${eng.ollama.default})`, true]);
  } else {
    opts.push(["", "Ollama 꺼짐 — 새 터미널에서 ollama serve", true]);
  }
  for (const [m, label] of eng.claude.models) {
    opts.push([`anthropic|${m}`, `${label} (API 과금)` + (eng.claude.has_key ? "" : " — API 키 없음"), !eng.claude.has_key]);
  }
  opts.push(["mock|", "모의 응답 (실제 AI 아님, 샘플 전용)"]);
  if (sel.dataset.sig === JSON.stringify([opts, cur])) return;
  sel.dataset.sig = JSON.stringify([opts, cur]);
  sel.replaceChildren(...opts.map(([v, l, dis]) => h("option", { value: v, disabled: !!dis, selected: v === cur }, l)));
}

$("#engine-apply").addEventListener("click", () => act(async () => {
  const [p, m] = $("#engine-select").value.split("|");
  if (!p) return;
  const body = p === "mock" ? { provider: "ollama", mode: "mock" } : { provider: p, model: m, mode: "live" };
  const r = await api("/api/engine", body);
  toast(`AI 엔진: ${r.label}`);
  await refresh();
}));
$("#reset").addEventListener("click", () => act(async () => {
  if (!confirm("처리한 결과를 모두 지우고 처음 상태로 돌아갈까요?")) return;
  await api("/api/reset", {});
  SELECTED = null;
  toast("처음 상태로 돌아왔습니다");
  await refresh();
}));
$("#job-close").addEventListener("click", () => ($("#job").hidden = true));
$("#modal-close").addEventListener("click", () => ($("#modal").hidden = true));
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#modal").hidden = true; });
document.querySelectorAll(".side button").forEach((b) => b.addEventListener("click", () => go(b.dataset.page)));

function go(page) {
  PAGE = page;
  document.querySelectorAll(".side button").forEach((b) => b.classList.toggle("on", b.dataset.page === page));
  render();
  $("#page").focus({ preventScroll: true });
}

function modal(title, ...body) {
  $("#modal-title").textContent = title;
  $("#modal-body").replaceChildren(...body);
  $("#modal").hidden = false;
}

async function showCall(no) {
  const { calls } = await api("/api/history");
  const c = calls.find((x) => x.no === no);
  if (!c) return;
  const text = (c.content || []).map((b) => (b.type === "text" ? b.text : JSON.stringify(b))).join("\n\n");
  modal(`AI 호출 #${c.no} — ${c.title}`,
    h("p", { class: "muted" }, `${c.mode_label} · 모델 ${c.model} · 입력 ${won(c.input_tokens)} / 출력 ${won(c.output_tokens)} 토큰 · ${c.duration_s}초 · $${c.cost_usd}`),
    h("h3", {}, "AI에게 준 규칙(시스템 프롬프트)"), h("pre", {}, c.system),
    h("h3", {}, "AI에게 보낸 내용"), h("pre", {}, text || "(모의 응답 — 보낸 내용 없음)"),
    h("h3", {}, "AI 응답(형식이 고정된 JSON)"), h("pre", {}, JSON.stringify(c.output, null, 2)),
    h("details", {}, h("summary", {}, "출력 형식(JSON Schema) 보기"), h("pre", {}, JSON.stringify(c.schema, null, 2))));
}

function render() {
  if (!STATE) return;
  const pages = { collect: pageCollect, match: pageMatch, review: pageReview, screen: pageScreen, ask: pageAsk, log: pageLog };
  (pages[PAGE] || pageCollect)();
}

// ───────────────────────── 1. 전산 수집
function pageCollect() {
  const t = STATE.todo, e = STATE.engine;
  const allDone = STATE.sources.filter((s) => s.sample).every((s) => s.status !== "대기" && s.status !== "오류");
  const todo = (n, label, page) => h("a", { class: n ? "" : "zero", onclick: () => go(page) }, `${label} `, h("b", {}, n), "건 →");
  const page = h("div", {},
    h("h2", {}, "1. 전산 수집 — 흩어진 자료를 모읍니다"),
    h("p", { class: "lead" }, "통신 3사·렌탈·상조 전산이 엑셀, 카톡, API, CSV, PDF, 메일로 제각각 들어옵니다. ",
      "AI는 사람이 손으로 옮기던 부분만 읽고(제안), 코드가 원문과 대조하고, 확정은 사람이 합니다."),
    h("div", { class: "card" },
      h("div", { class: "row" },
        h("button", { class: "btn primary big", disabled: allDone, onclick: () => runJob("/api/process_all") },
          allDone ? "샘플 전산 처리 완료" : "▶ 샘플 전산 모두 자동 처리"),
        h("span", { class: "muted" }, `AI 엔진: ${e.label}`),
        e.mode === "mock" ? h("span", { class: "chip warn" }, "모의 응답 — 실제 AI 결과 아님") : null),
      h("div", { class: "todo" },
        h("span", { class: "muted" }, "사람이 할 일:"),
        todo(t.mapping, "엑셀 매핑 승인", "collect"), todo(t.matching, "상품명 확인", "match"),
        todo(t.review, "값 검수", "review"), todo(t.to_promote, "확정 반영 대기", "review"),
        h("span", { class: "muted" }, `· ${STATE.month} 확정 ${won(STATE.confirmed)}건`))),
    h("div", { class: "grid" }, STATE.sources.map(sourceCard)),
    uploadCard(),
    h("div", { id: "detail" }));
  $("#page").replaceChildren(page);
  if (SELECTED) showSource(SELECTED);
}

function statusChip(st) {
  const cls = { "완료": "good", "매핑 승인 대기": "warn", "오류": "bad", "처리 중": "accent" }[st] || "";
  return h("span", { class: `chip ${cls}` }, st);
}

function sourceCard(s) {
  const canRun = s.status === "대기" || s.status === "오류";
  return h("div", { class: "card src" + (SELECTED === s.id ? " sel" : "") },
    h("div", { class: "row" }, h("span", { class: "t" }, s.title), s.sample ? null : h("span", { class: "chip ai" }, "올린 자료")),
    h("div", { class: "how" }, s.how), h("div", { class: "muted small" }, s.desc),
    h("div", { class: "foot" }, statusChip(s.status),
      canRun ? h("button", { class: "btn", onclick: () => { SELECTED = s.id; runJob(`/api/process/${s.id}`, {}, () => showSource(s.id)); } }, "처리")
        : h("button", { class: "btn", onclick: () => { SELECTED = s.id; render(); } },
          s.status === "매핑 승인 대기" ? "매핑 확인 →" : "결과 보기")));
}

function uploadCard() {
  const kinds = STATE.upload_kinds;
  const kindSel = h("select", {}, Object.entries(kinds).map(([k, v]) => h("option", { value: k }, v.label)));
  const partnerSel = h("select", {});
  const file = h("input", { type: "file" });
  const text = h("textarea", { placeholder: "카톡·문자 공지나 메일 본문을 붙여넣어도 됩니다.\n예) ▶ 번호이동\n갤S24 256 : 플래티넘 60개 / 베이직플러스 45개 / 세이브 30개" });
  const accept = { xlsx: ".xlsx", kakao: ".txt", csv: ".csv", pdf: ".pdf", email: ".txt,.eml" };
  const sync = () => {
    partnerSel.replaceChildren(...kinds[kindSel.value].partners.map((p) => h("option", { value: p }, p)));
    file.accept = accept[kindSel.value];
    text.hidden = !["kakao", "email"].includes(kindSel.value);
  };
  kindSel.addEventListener("change", sync);
  sync();
  const send = () => act(async () => {
    const body = { kind: kindSel.value, partner: partnerSel.value };
    if (file.files[0]) {
      const buf = new Uint8Array(await file.files[0].arrayBuffer());
      let bin = "";
      for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
      body.data_b64 = btoa(bin);
      body.filename = file.files[0].name;
    } else if (!text.hidden && text.value.trim()) {
      body.text = text.value;
      body.filename = kindSel.value === "kakao" ? "붙여넣은 카톡 공지.txt" : "붙여넣은 메일.txt";
    } else {
      throw new Error("파일을 고르거나 내용을 붙여넣으세요.");
    }
    await runJob("/api/upload", body, (r, res) => { SELECTED = res.source_id; });
  });
  return h("details", { class: "card" },
    h("summary", {}, "내 자료 올려서 처리해 보기 (Ollama·Claude 엔진에서)"),
    h("p", { class: "muted small" }, "카톡 공지를 직접 고쳐 붙여넣으면 AI가 어떻게 읽는지 바로 볼 수 있습니다. ",
      "올린 자료는 이 PC 안에서만 처리됩니다(Claude 엔진을 고르면 해당 내용이 Anthropic API로 전송됩니다)."),
    h("div", { class: "row" }, "종류", kindSel, "파트너", partnerSel, file),
    text, h("div", { class: "row", style: "margin-top:8px" }, h("button", { class: "btn primary", onclick: send }, "올리고 AI로 처리")));
}

async function showSource(id) {
  SELECTED = id;
  const d = await act(() => api(`/api/source/${id}`));
  const box = $("#detail");
  if (!d || !box) return;
  const r = d.result || { steps: [], tables: [], ai_calls: [] };
  const parts = [h("h2", {}, d.title), h("p", { class: "lead" }, d.how, " · ", statusChip(d.status))];
  parts.push(h("div", { class: "card" }, h("h3", { style: "margin-top:0" }, "처리 과정"),
    h("ol", { class: "steps" }, r.steps.map((s) => h("li", {}, `${ACTOR[s.actor] || "•"} `, h("b", {}, s.actor), " ", s.text))),
    r.ai_calls.length ? h("div", { class: "row" }, h("span", { class: "muted" }, "AI 호출 원문:"),
      r.ai_calls.map((n) => h("button", { class: "btn", onclick: () => showCall(n) }, `#${n} 보기`))) : null));
  if (d.mapping) parts.push(mappingEditor(d));
  for (const t of r.tables) {
    parts.push(h("div", { class: "card" }, t.text !== undefined
      ? h("details", {}, h("summary", {}, t.title), h("pre", {}, t.text)) : table(t)));
  }
  box.replaceChildren(...parts);
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

function mappingEditor(d) {
  const m = d.mapping, o = m.options;
  const sel = (opts, v) => h("select", {}, opts.map(([k, l]) => h("option", { value: k, selected: k === v }, l)));
  const rows = m.columns.map((c) => {
    const f = sel(o.fields, c.field_code), p = sel(o.plans, c.plan_id), cond = sel(o.conditions, c.condition_key), u = sel(o.units, c.unit);
    const tr = h("tr", { class: c.passed ? "" : "bad" },
      h("td", {}, c.col), h("td", {}, c.actual),
      h("td", {}, c.header_ok === false ? `❌ ${c.ai_header}` : c.ai_header),
      h("td", {}, f), h("td", {}, p), h("td", {}, cond), h("td", {}, u),
      h("td", { class: "num" }, c.median === null ? "" : `${won(c.median)}원`),
      h("td", {}, c.range_ok === null ? "➖" : c.range_ok ? "✅" : "❌ 범위 밖"));
    tr._get = () => ({ col: c.col, field_code: f.value, plan_id: p.value, condition_key: cond.value, unit: u.value });
    return tr;
  });
  const hr = h("input", { type: "text", value: m.header_rows.join(","), size: 6 });
  const approve = () => runJob(`/api/mapping/${d.id}`, {
    columns: rows.map((r) => r._get()), header_rows: hr.value.split(",").map((x) => parseInt(x, 10)).filter((x) => x > 0),
  }, () => showSource(d.id));
  return h("div", { class: "card" },
    h("h3", { style: "margin-top:0" }, "🙋 AI가 제안한 열 매핑 — 확인하고 승인하세요"),
    h("p", { class: "muted small" }, "빨간 줄은 코드 검증에 걸린 열입니다(열 제목 불일치, 사전에 없는 요금제·조건, 단위를 적용한 값이 정상 범위 밖). ",
      "틀린 칸은 고친 뒤 승인하세요. 승인하면 규칙 파서가 모든 행의 값을 읽습니다."),
    h("div", { class: "row" }, "열 제목 행", hr, m.checks.map((c) => h("span", { class: `chip ${c.ok ? "good" : "bad"}` }, `${c.ok ? "✅" : "❌"} ${c.name}`))),
    h("div", { class: "scroll" }, h("table", {},
      h("thead", {}, h("tr", {}, ["열", "실제 열 제목", "AI가 적은 제목", "항목", "요금제", "조건", "단위", "단위 적용 후 중앙값", "범위"].map((x) => h("th", {}, x)))),
      h("tbody", {}, rows))),
    h("div", { class: "row", style: "margin-top:10px" }, h("button", { class: "btn primary", onclick: approve }, "매핑 승인 → 값 읽기")));
}

// ───────────────────────── 2. 상품명 매칭
async function pageMatch() {
  const list = await act(() => api("/api/matches"));
  if (!list || PAGE !== "match") return;
  const noAi = list.filter((m) => !m.suggestion).length;
  const page = h("div", {},
    h("h2", {}, "2. 상품명 매칭 — 파트너마다 다른 이름을 표준 상품에 연결"),
    h("p", { class: "lead" }, "규칙(승인된 이름·연동 코드·모델코드·정규화)으로 못 푼 이름만 여기 옵니다. ",
      "AI는 코드가 뽑은 후보 중에서 고르기만 하고, 확정은 사람이 합니다. 확정한 이름은 다음부터 AI 없이 매칭됩니다."),
    h("div", { class: "row" },
      noAi ? h("button", { class: "btn", onclick: () => runJob("/api/matches/ai") }, `AI 판정 받기 (${noAi}건)`) : null,
      list.length ? h("button", { class: "btn", onclick: () => runJob("/api/matches/confirm_all") }, "AI 제안대로 모두 확정") : null));
  if (!list.length) {
    page.append(h("div", { class: "card empty" }, "확인할 상품명이 없습니다. ",
      STATE.todo.review ? h("a", { onclick: () => go("review"), style: "cursor:pointer;color:var(--accent)" }, "값 검수로 →") : "먼저 전산을 수집하세요."));
  } else {
    page.append(h("div", { class: "card scroll" }, h("table", {},
      h("thead", {}, h("tr", {}, ["파트너", "원래 이름", "AI 제안", "AI 근거(참고)", "유사도 1순위", "확정할 상품", ""].map((x) => h("th", {}, x)))),
      h("tbody", {}, list.map(matchRow)))));
  }
  $("#page").replaceChildren(page);
}

function matchRow(m) {
  const s = m.suggestion;
  const pick = s ? (s.product_id || (["new_product_candidate", "no_match"].includes(s.decision) ? "NEW" : "")) : "";
  const sel = h("select", {},
    h("option", { value: "" }, "— 고르세요 —"),
    m.candidates.map((c) => h("option", { value: c.id, selected: c.id === pick }, `${c.name} (유사도 ${c.similarity})`)),
    h("option", { value: "NEW", selected: pick === "NEW" }, "신규 상품 등록 요청(이번 달 반영 제외)"));
  const aiText = !s ? h("span", { class: "muted" }, "AI 판정 전")
    : h("span", {}, s.product_id ? (m.candidates.find((c) => c.id === s.product_id) || {}).name : { new_product_candidate: "신규 상품 후보", no_match: "해당 없음", ambiguous: "애매함", no_answer: "응답 없음" }[s.decision] || s.decision,
      " ", h("span", { class: `chip ${s.grade === "high" ? "good" : s.grade === "medium" ? "warn" : "bad"}` }, s.grade));
  const top = m.candidates[0];
  return h("tr", {},
    h("td", {}, m.partner), h("td", {}, h("b", {}, m.raw_name), m.model_code && m.model_code !== "-" ? h("div", { class: "muted small" }, m.model_code) : null),
    h("td", {}, aiText), h("td", { class: "small" }, s ? s.reason : ""), h("td", { class: "small muted" }, top ? top.name : ""),
    h("td", {}, sel),
    h("td", {}, h("button", { class: "btn good", onclick: () => { if (!sel.value) return toast("확정할 상품을 고르세요", true); runJob(`/api/matches/${m.id}`, { choice: sel.value }); } }, "확정")));
}

// ───────────────────────── 3. 값 검수·확정
async function pageReview() {
  const r = await act(() => api("/api/review"));
  if (!r || PAGE !== "review") return;
  const g = r.groups;
  const needExplain = [...g.blocked, ...g.needs].some((x) => x.anomalies.some((a) => !a.explanation));
  const page = h("div", {},
    h("h2", {}, "3. 값 검수·확정 — 사람이 확인한 값만 확정 DB로"),
    h("p", { class: "lead" }, "이상 여부는 규칙이 정하고, AI는 이유를 설명만 합니다. 리베이트처럼 돈이 오가는 값이 바뀌면 규칙 위반이 없어도 사람이 승인해야 합니다. ",
      "차단(block)된 값은 그대로 승인할 수 없습니다."),
    h("div", { class: "kpi" },
      h("div", {}, h("b", { style: "color:var(--bad)" }, g.blocked.length), "차단"),
      h("div", {}, h("b", { style: "color:var(--warn)" }, g.needs.length), "사람 확인 필요"),
      h("div", {}, h("b", {}, g.safe.length), "일괄 승인 가능"),
      h("div", {}, h("b", { style: "color:var(--good)" }, g.approved.length), "승인됨(반영 대기)"),
      h("div", {}, h("b", {}, g.promoted.length), "확정 반영 완료"),
      r.waiting_for_match ? h("div", {}, h("b", {}, r.waiting_for_match), "매칭 확인 대기") : null),
    h("div", { class: "row" },
      h("button", { class: "btn", disabled: !g.safe.length, onclick: () => act(async () => { const x = await api("/api/review/approve_safe", {}); toast(`${x.approved}건 일괄 승인`); await refresh(); }) },
        `✅ 규칙·검증 통과 ${g.safe.length}건 일괄 승인`),
      needExplain ? h("button", { class: "btn", onclick: () => runJob("/api/review/explain") }, "🤖 이상 건 AI 설명 받기") : null,
      h("button", { class: "btn primary", disabled: !g.approved.length, onclick: () => act(async () => {
        const x = await api("/api/promote", {});
        toast(`${x.promoted}건 확정 반영 → 계산 완료. 요금 설계 화면에 반영됐습니다`);
        await refresh();
      }) }, `🗄️ 승인된 ${g.approved.length}건 확정 반영 → 계산`)));
  const section = (title, rows, opts) => rows.length ? h(opts.collapsed ? "details" : "div", { class: "card" },
    opts.collapsed ? h("summary", {}, `${title} (${rows.length})`)
      : h("div", { class: "row" }, h("h3", { style: "margin:0" }, `${title} (${rows.length})`), opts.extra || null),
    reviewTable(rows, opts.actions)) : null;
  const approveNeeds = h("button", { class: "btn good", onclick: () => act(async () => {
    if (!confirm(`'사람 확인 필요' ${g.needs.length}건을 모두 확인하셨나요? 한꺼번에 승인합니다.`)) return;
    const x = await api("/api/review/approve_group", { group: "needs" });
    toast(`${x.approved}건 승인`);
    await refresh();
  }) }, "이 구역 모두 승인");
  page.append(...[
    section("🚫 차단 — 값을 고치거나 제외", g.blocked, { actions: ["edit", "reject"] }),
    section("🙋 사람 확인 필요", g.needs, { actions: ["approve", "edit", "reject"], extra: approveNeeds }),
    section("✅ 규칙·검증 통과(일괄 승인 가능)", g.safe, { collapsed: true, actions: ["approve", "edit", "reject"] }),
    section("승인됨 — 확정 반영 대기", g.approved, { collapsed: true, actions: ["undo"] }),
    section("제외됨", g.excluded, { collapsed: true, actions: ["undo"] }),
    section("확정 반영 완료", g.promoted, { collapsed: true, actions: [] }),
    manualForm(r)].filter(Boolean));
  if (!g.blocked.length && !g.needs.length && !g.safe.length && !g.approved.length && !g.promoted.length) {
    page.append(h("div", { class: "card empty" }, "검수할 값이 없습니다. 먼저 전산을 수집하고 상품명 매칭을 확정하세요."));
  }
  $("#page").replaceChildren(page);
}

function reviewTable(rows, actions) {
  const tr = (x) => {
    const val = h("input", { type: "text", placeholder: "예: 55개" });
    const btns = [];
    const send = (action, value) => act(async () => { await api(`/api/review/${x.id}`, { action, value }); await refresh(); });
    if (actions.includes("approve")) btns.push(h("button", { class: "btn good", onclick: () => send("approve") }, "승인"));
    if (actions.includes("edit")) btns.push(val, h("button", { class: "btn", onclick: () => send("edit", val.value) }, "수정 승인"));
    if (actions.includes("reject")) btns.push(h("button", { class: "btn bad", onclick: () => send("reject") }, "제외"));
    if (actions.includes("undo")) btns.push(h("button", { class: "btn ghost", onclick: () => send("undo") }, "되돌리기"));
    const cls = x.group === "blocked" ? "bad" : x.group === "needs" ? "warn" : "";
    return h("tr", { class: cls },
      h("td", {}, x.partner), h("td", {}, x.product),
      h("td", {}, x.field, h("div", { class: "muted small" }, x.condition)),
      h("td", { class: "num" }, won(x.prev), x.is_rebate && x.prev !== null ? h("div", { class: "muted small" }, gae(x.prev)) : null),
      h("td", { class: "num" }, h("b", {}, won(x.value)), x.is_rebate ? h("div", { class: "muted small" }, gae(x.value)) : null),
      h("td", { class: "num" }, x.change_pct === null ? "" : `${x.change_pct > 0 ? "+" : ""}${x.change_pct}%`),
      h("td", { class: "small" }, x.source, h("div", { class: "muted" }, `${x.extractor} · ${x.grade}`)),
      h("td", { class: "small", title: x.evidence || "" },
        x.reasons.map((r) => h("span", { class: "reason" }, r)),
        x.anomalies.map((a) => h("span", { class: "reason" }, `${a.rule}(${a.severity}) ${a.reason}`)),
        x.anomalies.filter((a) => a.explanation).slice(0, 1).map((a) => h("span", { class: "expl" }, `🤖 ${a.cause}: ${a.explanation}`)),
        x.review_note ? h("span", { class: "muted" }, x.review_note) : null),
      h("td", { class: "act" }, btns));
  };
  return h("div", { class: "scroll" }, h("table", {},
    h("thead", {}, h("tr", {}, ["파트너", "상품", "항목", "지난달", "새 값", "변동", "출처", "이유·이상", "동작"].map((c, i) => h("th", { class: i >= 3 && i <= 5 ? "num" : "" }, c)))),
    h("tbody", {}, rows.map(tr))));
}

function manualForm(r) {
  const partner = h("select", {}, r.partners.map((p) => h("option", { value: p }, p)));
  const product = h("select", {}, r.products.map((p) => h("option", { value: p.id }, `${p.name}`)));
  const plan = h("select", {});
  const field = h("select", {}, r.fields.map(([k, l]) => h("option", { value: k }, l)));
  const cond = h("select", {}, r.conditions.map(([k, l]) => h("option", { value: k }, l)));
  const value = h("input", { type: "text", placeholder: "예: 55개 또는 550000" });
  const syncPlans = () => plan.replaceChildren(h("option", { value: "" }, "요금제 무관"),
    ...r.plans.filter((p) => p.partner === partner.value).map((p) => h("option", { value: p.plan_id }, `${p.name} ${won(p.monthly_fee)}원`)));
  partner.addEventListener("change", syncPlans);
  syncPlans();
  const add = () => act(async () => {
    const x = await api("/api/review/manual", { partner: partner.value, product_id: product.value, plan_id: plan.value, field_code: field.value, condition_key: cond.value, value: value.value });
    toast(`직접 입력 ${won(x.value)}원 — 승인됨`);
    await refresh();
  });
  return h("details", { class: "card" }, h("summary", {}, "값 직접 추가 (AI가 빠뜨린 값을 원문 보고 입력)"),
    h("div", { class: "row", style: "margin-top:8px" }, partner, product, plan, field, cond, value, h("button", { class: "btn", onclick: add }, "추가(승인됨)")));
}

// ───────────────────────── 4. 요금 설계 화면
function pageScreen() {
  const frame = h("iframe", { class: "iframe", src: `/screen?t=${Date.now()}`, title: "휴대폰 요금 설계 화면" });
  $("#page").replaceChildren(h("div", {},
    h("div", { class: "row" }, h("h2", { style: "margin:0" }, "4. 휴대폰 요금 설계 화면"),
      h("a", { class: "btn", href: "/screen", target: "_blank", rel: "noopener" }, "새 창으로 열기"),
      h("span", { class: "muted" }, `확정 반영 ${STATE.promotions}회 · 확정 데이터와 계산 결과만 읽습니다`)),
    h("p", { class: "lead" }, "단말을 고르면 통신 3사의 요금제별 공시지원금, 월 납부액(두 방식), 요금제×가입유형별 리베이트(개)가 한 화면에 모입니다. ",
      "[고객 안내 화면]을 누르면 리베이트가 숨겨집니다."),
    frame));
}

// ───────────────────────── 5. 자연어 조회
function pageAsk() {
  const q = h("input", { type: "text", placeholder: "예: A통신 115요금제 번호이동 리베이트가 50개 이상인 단말 보여줘", style: "flex:1;min-width:280px" });
  const out = h("div", { id: "ask-out" });
  const show = (res) => {
    const parts = [h("div", { class: "card" }, h("p", { class: "muted", style: "margin-top:0" }, `질문: “${res.question}”`),
      h("h3", {}, "🤖 AI 응답 — 조회 종류 + 조건"),
      h("pre", {}, JSON.stringify(res.ai, null, 2)), h("button", { class: "btn", onclick: () => showCall(res.call) }, "AI 호출 원문 보기"))];
    if (res.resolved.length) parts.push(h("div", { class: "card" }, "⚙️ 코드가 사전으로 해석: ", res.resolved.join(" / ")));
    if (res.table) parts.push(h("div", { class: "card" }, table(res.table)));
    if (res.unsupported) parts.push(h("div", { class: "card" }, "⚙️ 지원하지 않는 질문 → 조회도 계산도 하지 않습니다. AI가 적은 사유: ", res.unsupported));
    out.replaceChildren(...parts);
  };
  const ask = () => runJob("/api/nlq", { question: q.value }, (res) => { LAST_ASK = { ...res, question: q.value }; });
  if (LAST_ASK) show(LAST_ASK);
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") ask(); });
  $("#page").replaceChildren(h("div", {},
    h("h2", {}, "5. 자연어 조회"),
    h("p", { class: "lead" }, "AI는 질문을 '정해진 조회 종류 + 조건'으로 바꾸기만 합니다. SQL을 쓰지 않고, 표의 숫자는 확정 DB에서 나옵니다."),
    h("div", { class: "card" }, h("div", { class: "row" }, q, h("button", { class: "btn primary", onclick: ask }, "질문")),
      h("div", { class: "row", style: "margin-top:8px" }, h("span", { class: "muted small" }, "예시:"),
        STATE.sample_questions.map((s) => h("button", { class: "btn ghost small", onclick: () => { q.value = s; ask(); } }, s)))),
    out));
}

// ───────────────────────── 6. AI 기록
async function pageLog() {
  const d = await act(() => api("/api/history"));
  if (!d || PAGE !== "log") return;
  const tin = d.calls.reduce((a, c) => a + c.input_tokens, 0), tout = d.calls.reduce((a, c) => a + c.output_tokens, 0);
  const cost = d.calls.reduce((a, c) => a + c.cost_usd, 0), secs = d.calls.reduce((a, c) => a + c.duration_s, 0);
  $("#page").replaceChildren(h("div", {},
    h("h2", {}, "6. AI 기록 — AI에게 무엇을 보내고 무엇을 받았나"),
    h("p", { class: "lead" }, `AI 호출 ${d.calls.length}회 · 입력 ${won(tin)} / 출력 ${won(tout)} 토큰 · ${secs.toFixed(1)}초 · 비용 $${cost.toFixed(4)}`),
    d.calls.length ? h("div", { class: "card scroll" }, h("table", {},
      h("thead", {}, h("tr", {}, ["#", "단계", "내용", "엔진", "입력", "출력", "시간(초)", "비용($)", ""].map((x) => h("th", {}, x)))),
      h("tbody", {}, d.calls.slice().reverse().map((c) => h("tr", {},
        h("td", {}, c.no), h("td", { class: "small" }, c.step_id), h("td", {}, c.title), h("td", { class: "small" }, c.mode_label),
        h("td", { class: "num" }, won(c.input_tokens)), h("td", { class: "num" }, won(c.output_tokens)),
        h("td", { class: "num" }, c.duration_s), h("td", { class: "num" }, c.cost_usd.toFixed(4)),
        h("td", {}, h("button", { class: "btn", onclick: () => showCall(c.no) }, "원문"))))))) : h("div", { class: "card empty" }, "아직 AI 호출이 없습니다."),
    h("h3", {}, "사람이 한 일(감사 로그)"),
    d.audit.length ? h("div", { class: "card scroll" }, table({ title: "", columns: ["#", "누가", "무엇을", "내용"], rows: d.audit.map((a) => [a.id, a.actor, a.action, a.detail]) }))
      : h("div", { class: "card empty" }, "아직 기록이 없습니다.")));
}

// ───────────────────────── 시작
go("collect");
refresh().catch((e) => toast(e.message, true));
setInterval(() => { if (!document.hidden && $("#job").hidden) api("/api/state").then((s) => { STATE = s; const t = s.todo; $("#b-match").textContent = t.matching || ""; $("#b-review").textContent = (t.review + t.to_promote) || ""; $("#b-collect").textContent = t.mapping || ""; }).catch(() => {}); }, 5000);
