"""Single-file office workbench for the first meeting-minutes slice."""

from __future__ import annotations

OFFICE_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spark Office · 会议纪要与行动项</title>
<style>
:root{
  --bg:#f2f2f4; --panel:#ffffff; --panel2:#f7f7f9; --line:#dcdce2; --line-2:#c2c2cb;
  --ink:#14141a; --ink2:#4a4a55; --ink3:#86868f;
  --accent:#e8620c; --accent2:#1d4ed8; --ok:#0f9d58; --warn:#b45309; --err:#d92d20;
}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;background:radial-gradient(1200px 600px at 80% -10%,#ffffff 0%,var(--bg) 55%);color:var(--ink);
  font:14px/1.55 -apple-system,BlinkMacSystemFont,"Inter","Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans SC",sans-serif;min-height:100vh}
a{color:var(--accent2)}
header{display:flex;align-items:center;gap:14px;padding:18px 26px;border-bottom:1px solid var(--line);
  background:rgba(255,255,255,.86);backdrop-filter:blur(8px);position:sticky;top:0;z-index:5}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;letter-spacing:.3px}
.dot{width:10px;height:10px;border-radius:50%;background:var(--accent);box-shadow:0 0 18px var(--accent)}
.sub{color:var(--ink3);font-size:12px}
.spacer{flex:1}
.tabs{display:flex;gap:8px;margin:0 0 16px}
.tab{background:transparent;border:1px solid var(--line);color:var(--ink2);border-radius:9px;padding:7px 13px}
.tab.active{background:#fff;border-color:var(--accent);color:var(--accent)}
.token{display:flex;align-items:center;gap:8px;color:var(--ink3);font-size:12px}
.token input{width:230px;background:#fff;border:1px solid var(--line);color:var(--ink);border-radius:8px;
  padding:7px 9px;font-family:ui-monospace,Consolas,monospace;font-size:11px}
main{max-width:1180px;margin:0 auto;padding:24px 22px 60px}
.hero{display:flex;align-items:flex-end;justify-content:space-between;gap:20px;margin:8px 0 20px}
.hero h1{font-size:26px;margin:0 0 6px;letter-spacing:.2px}
.hero p{margin:0;color:var(--ink2);max-width:680px}
.badge{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:var(--panel);
  color:var(--ink2);border-radius:999px;padding:5px 10px;font-size:12px;white-space:nowrap}
.badge.ok{color:var(--ok);border-color:#b7e1c8;background:#edf9f2}
.grid{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,.95fr);gap:18px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  box-shadow:0 10px 34px rgba(16,24,40,.08)}
.card-h{display:flex;align-items:center;gap:10px;padding:14px 16px;border-bottom:1px solid var(--line)}
.card-h h2{font-size:14px;margin:0;font-weight:650}
.card-b{padding:16px}
label{display:block;color:var(--ink2);font-size:12px;margin:0 0 6px}
input[type=text],textarea,select{width:100%;background:#fff;border:1px solid var(--line);border-radius:9px;
  color:var(--ink);padding:10px 11px;font:inherit;outline:none}
input[type=file]{width:100%;background:#fff;border:1px dashed var(--line);border-radius:9px;
  padding:8px;font:inherit;font-size:12px;color:var(--ink2)}
input[type=text]:focus,textarea:focus,select:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(232,98,12,.12)}
textarea{min-height:240px;resize:vertical;font-family:ui-monospace,Consolas,monospace;font-size:12px;line-height:1.6;background:#fbfbfc}
.row{display:grid;grid-template-columns:1fr 190px;gap:12px;margin-bottom:12px}
.report-meta{grid-template-columns:1fr 1fr}
.actions{display:flex;align-items:center;gap:10px;margin-top:12px;flex-wrap:wrap}
button{border:1px solid var(--line);background:var(--panel2);color:var(--ink);border-radius:9px;padding:9px 13px;
  font:inherit;cursor:pointer;transition:.15s ease}
button:hover{border-color:var(--line-2,#c2c2cb);background:#e4e4e9}
button.primary{background:linear-gradient(180deg,#f07a2a,#e8620c);border-color:#e8620c;color:#fff;font-weight:700}
#report-result .actions button{padding:7px 9px;font-size:12px}
button.primary:hover{filter:brightness(1.06)}
button.ghost{background:transparent;color:var(--ink2)}
button:disabled{opacity:.55;cursor:not-allowed}
.muted{color:var(--ink3);font-size:12px}
.result-empty{min-height:320px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;
  color:var(--ink3);text-align:center;padding:30px}
.result-empty .big{font-size:34px;opacity:.5}
.summary{background:#fff7f1;border:1px solid #f3d6c5;border-left:3px solid var(--accent);border-radius:10px;
  padding:11px 13px;color:var(--ink2);margin-bottom:14px}
.ai-note{margin-top:8px;background:#f2f7ff;border:1px solid #d6e2f3;border-left:3px solid #2f5597;border-radius:10px;
  padding:10px 12px;font-size:12px;line-height:1.6;color:var(--ink2)}
.ai-note b{color:#2f5597}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{border:1px solid var(--line);background:var(--panel2);color:var(--ink2);border-radius:999px;padding:3px 9px;font-size:11px}
h3{font-size:12px;color:var(--ink3);text-transform:uppercase;letter-spacing:.9px;margin:16px 0 8px}
ul.clean{list-style:none;margin:0;padding:0}
ul.clean li{padding:8px 10px;border:1px solid var(--line);border-radius:9px;margin-bottom:7px;background:var(--panel)}
.line{color:var(--ink3);font-size:11px;margin-left:6px}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{text-align:left;padding:8px 9px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--ink3);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
table th:nth-child(2),table td:nth-child(2){width:60px;white-space:nowrap}
table th:nth-child(3),table td:nth-child(3){width:88px;white-space:nowrap}
table th:nth-child(4),table td:nth-child(4){width:58px;white-space:nowrap}
table td:first-child{word-break:break-word}
.pill{display:inline-block;border-radius:999px;padding:2px 8px;font-size:11px;border:1px solid var(--line);color:var(--ink2)}
.pill.approved{color:var(--ok);border-color:#b7e1c8;background:#edf9f2}
.pill.draft{color:var(--warn);border-color:#f0d9a8;background:#fff8e6}
.recent{margin-top:18px}
.recent-item{display:flex;align-items:center;gap:12px;padding:11px 14px;border-bottom:1px solid var(--line);cursor:pointer}
.recent-item:last-child{border-bottom:0}
.recent-item:hover{background:var(--panel2)}
.recent-title{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.toast{position:fixed;right:20px;bottom:20px;background:#fff;border:1px solid var(--line);border-radius:10px;
  padding:10px 13px;box-shadow:0 10px 30px rgba(16,24,40,.16);opacity:0;transform:translateY(8px);transition:.2s}
.toast.show{opacity:1;transform:none}
.toast.err{border-color:#f3c2bd;color:var(--err)}
@media(max-width:880px){.grid{grid-template-columns:1fr}.row{grid-template-columns:1fr}.hero{display:block}
  .token input{width:150px}main{padding:18px 14px 50px}}
</style>
</head>
<body>
<header>
  <div class="brand"><span class="dot"></span>Spark Office <span class="sub">/ 企业控制面</span></div>
  <div class="spacer"></div>
  <label class="token">Bearer Token（OIDC 时填写）
    <input id="token" type="password" placeholder="dev 模式留空" autocomplete="off">
  </label>
</header>
<main>
  <div class="hero">
    <div>
      <h1>会议纪要与行动项</h1>
      <p>粘贴会议转写，自动生成带来源行号的结构化纪要、行动项与可下载成果物；人工批准后才算定稿。</p>
    </div>
    <div class="badge ok" id="health">连接中…</div>
  </div>

  <div class="tabs">
    <button class="tab active" data-pane="meeting">会议纪要</button>
    <button class="tab" data-pane="report">报告 / 表格</button>
  </div>

  <div id="pane-meeting">
  <div class="grid">
    <section class="card">
      <div class="card-h"><h2>输入</h2><span class="spacer"></span>
        <button class="ghost" id="sample">填入示例</button>
      </div>
      <div class="card-b">
        <div class="row">
          <div><label>会议标题</label><input id="title" type="text" value="产品周会 · 企业办公工作台"></div>
          <div><label>项目</label><select id="project"><option value="">自动创建 Office 项目</option></select></div>
        </div>
        <label>会议转写 / 邮件线程</label>
        <textarea id="transcript" placeholder="每行一条发言或结论；行动项请写清负责人和截止时间。"></textarea>
        <label style="margin-top:10px">上传文档（可选，最大 25MB）</label>
        <input id="document-file" type="file" accept=".docx,.pdf,.pptx,.xlsx,.png,.jpg,.jpeg,.bmp,.tif,.tiff,.webp,.eml,.msg,.txt,.md,.csv,.json,.html,.htm">
        <input id="document" type="text" style="margin-top:6px" placeholder="或填 documents 目录中已有文件：meeting.docx / notes.pdf / scan.png / mail.msg">
        <div class="muted" id="upload-hint" style="margin:4px 0 0"></div>
        <div class="actions" style="margin-top:8px">
          <button class="ghost" id="imap-fetch">从 IMAP 拉取最新邮件</button>
          <span class="muted" id="imap-hint">需配置 SPARK_CONTROL_IMAP_*</span>
        </div>
        <div class="actions">
          <button class="primary" id="generate">生成纪要</button>
          <button class="ghost" id="clear">清空</button>
          <span class="muted" id="hint">确定性解析 + 可选模型摘要；支持 md/docx/pptx/pdf 导出。</span>
        </div>
      </div>
    </section>

    <section class="card">
      <div class="card-h"><h2>纪要结果</h2><span class="spacer"></span><span class="pill draft" id="status" hidden>draft</span></div>
      <div class="card-b" id="result">
        <div class="result-empty">
          <div class="big">◎</div>
          <div>还没有生成纪要</div>
          <div class="muted">左侧填入会议内容，点击“生成纪要”。</div>
        </div>
      </div>
    </section>
  </div>

  <section class="card recent">
    <div class="card-h"><h2>最近会议</h2><span class="spacer"></span><button class="ghost" id="refresh">刷新</button></div>
    <div id="recent"><div class="card-b muted">加载中…</div></div>
  </section>
  </div>

  <div id="pane-report" hidden>
    <div class="grid">
      <section class="card">
        <div class="card-h"><h2>报告输入</h2><span class="spacer"></span>
          <button class="ghost" id="report-sample">填入示例</button>
        </div>
        <div class="card-b">
          <div class="row report-meta">
            <div><label>报告标题</label><input id="report-title" type="text" value="Q4 办公效率报告"></div>
            <div><label>摘要（留空自动生成）</label><textarea id="report-summary" style="min-height:58px" placeholder="可选"></textarea></div>
          </div>
          <div class="row">
            <div><label>指标（每行：指标,实际,目标,单位）</label>
              <textarea id="report-metrics" style="min-height:180px" placeholder="会议纪要产出, 42, 30, 份
审批平均时长, 1.8, 2.5, 天"></textarea>
            </div>
            <div><label>章节（## 标题 + 正文）</label>
              <textarea id="report-sections" style="min-height:180px" placeholder="## 结论
本季度办公自动化覆盖率显著提升。"></textarea>
            </div>
          </div>
          <div class="actions">
            <button class="primary" id="report-generate">生成报告</button>
            <button class="ghost" id="report-clear">清空</button>
            <span class="muted">导出 Markdown / Excel（含图表）/ Word / PPTX / PDF</span>
          </div>
        </div>
      </section>
      <section class="card">
        <div class="card-h"><h2>报告结果</h2><span class="spacer"></span><span class="pill draft" id="report-status" hidden>draft</span></div>
        <div class="card-b" id="report-result">
          <div class="result-empty"><div class="big">▦</div><div>还没有生成报告</div>
            <div class="muted">左侧填入指标与章节，点击“生成报告”。</div></div>
        </div>
      </section>
    </div>
    <section class="card recent">
      <div class="card-h"><h2>最近报告</h2><span class="spacer"></span><button class="ghost" id="report-refresh">刷新</button></div>
      <div id="report-recent"><div class="card-b muted">加载中…</div></div>
    </section>
  </div>
</main>
<div class="toast" id="toast"></div>
<script>
const $ = (id) => document.getElementById(id);
const token = $("token");
let currentTask = "";

function authHeaders(extra = {}, json = true) {
  const headers = json ? { "Content-Type": "application/json", ...extra } : { ...extra };
  if (token.value.trim()) headers["Authorization"] = "Bearer " + token.value.trim();
  return headers;
}
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[ch]));
}
function toast(message, isError = false) {
  const el = $("toast");
  el.textContent = message;
  el.className = "toast show" + (isError ? " err" : "");
  setTimeout(() => { el.className = "toast"; }, 2600);
}
async function api(path, options = {}) {
  const isForm = options.body instanceof FormData;
  const res = await fetch(path, {
    ...options,
    headers: authHeaders(options.headers || {}, !isForm)
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!res.ok) {
    const detail = (data && (data.detail || data.error)) || res.status + " " + res.statusText;
    throw new Error(detail);
  }
  return data;
}
async function health() {
  try {
    const h = await api("/api/v1/health");
    $("health").textContent = "数据库 " + h.database + " · " + h.auth_mode + " 模式";
    $("health").className = "badge " + (h.status === "ok" ? "ok" : "");
  } catch (e) {
    $("health").textContent = "连接失败";
  }
}
async function loadProjects() {
  try {
    const projects = await api("/api/v1/projects");
    const select = $("project");
    select.innerHTML = '<option value="">自动创建 Office 项目</option>';
    for (const p of projects) {
      const opt = document.createElement("option");
      opt.value = p.id; opt.textContent = p.name;
      select.appendChild(opt);
    }
  } catch { /* dev/oidc auth failure is surfaced on submit */ }
}
function renderResult(data) {
  currentTask = data.task_id;
  const m = data.minutes || {};
  const artifacts = data.artifacts || [];
  $("status").hidden = false;
  $("status").textContent = data.status || "draft";
  $("status").className = "pill " + (data.status || "draft");
  const attendees = (m.attendees || []).map(x => `<span class="chip">${escapeHtml(x)}</span>`).join("");
  const topics = (m.topics || []).map(x => `<li>${escapeHtml(x)}</li>`).join("");
  const decisions = (m.decisions || []).map(x =>
    `<li>${escapeHtml(x.text)}<span class="line">第 ${escapeHtml(x.line)} 行</span></li>`).join("");
  const actions = (m.actions || []).map(x =>
    `<tr><td>${escapeHtml(x.text)}</td><td>${escapeHtml(x.owner || "待确认")}</td>
     <td>${escapeHtml(x.due || "待确认")}</td><td>第 ${escapeHtml(x.line)} 行</td></tr>`).join("");
  const md = artifacts.some(a => a.kind === "meeting_minutes_md");
  const docx = artifacts.some(a => a.kind === "meeting_minutes_docx");
  const pptx = artifacts.some(a => a.kind === "meeting_minutes_pptx");
  const pdf = artifacts.some(a => a.kind === "meeting_minutes_pdf");
  $("result").innerHTML = `
    <div class="summary">${escapeHtml(m.summary || "")}</div>
    ${m.ai_summary ? `<div class="ai-note"><b>AI 摘要</b><br>${escapeHtml(m.ai_summary)}</div>` : ""}
    ${attendees ? `<h3>参会人员</h3><div class="chips">${attendees}</div>` : ""}
    ${topics ? `<h3>议题</h3><ul class="clean">${topics}</ul>` : ""}
    ${decisions ? `<h3>决议与结论</h3><ul class="clean">${decisions}</ul>` : ""}
    ${actions ? `<h3>行动项</h3><table><thead><tr><th>行动</th><th>负责人</th><th>截止</th><th>来源</th></tr></thead>
      <tbody>${actions}</tbody></table>` : ""}
    <div class="actions">
      ${md ? '<button id="dl-md">下载 Markdown</button>' : ""}
      ${docx ? '<button id="dl-docx">下载 docx</button>' : ""}
      ${pptx ? '<button id="dl-pptx">下载 PPTX</button>' : ""}
      ${pdf ? '<button id="dl-pdf">下载 PDF</button>' : ""}
      ${data.status !== "approved" ? '<button class="primary" id="approve">批准纪要</button>' : '<span class="badge ok">已批准</span>'}
    </div>`;
  if (md) $("dl-md").onclick = () => download(currentTask, "md");
  if (docx) $("dl-docx").onclick = () => download(currentTask, "docx");
  if (pptx) $("dl-pptx").onclick = () => download(currentTask, "pptx");
  if (pdf) $("dl-pdf").onclick = () => download(currentTask, "pdf");
  if ($("approve")) $("approve").onclick = approveCurrent;
}
async function download(taskId, format) {
  try {
    const res = await fetch(`/api/v1/office/meetings/${taskId}/download?format=${format}`,
      { headers: authHeaders() });
    if (!res.ok) throw new Error(await res.text());
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "minutes." + format;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    toast("已下载 minutes." + format);
  } catch (e) { toast("下载失败：" + e.message, true); }
}
async function approveCurrent() {
  try {
    const data = await api(`/api/v1/office/meetings/${currentTask}/approve`, { method: "POST", body: "{}" });
    renderResult(data); loadMeetings(); toast("纪要已批准");
  } catch (e) { toast("批准失败：" + e.message, true); }
}
async function openMeeting(taskId) {
  try {
    const data = await api(`/api/v1/office/meetings/${taskId}`);
    renderResult(data); window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (e) { toast("加载失败：" + e.message, true); }
}
async function loadMeetings() {
  try {
    const rows = await api("/api/v1/office/meetings");
    if (!rows.length) {
      $("recent").innerHTML = '<div class="card-b muted">还没有会议纪要。</div>';
      return;
    }
    $("recent").innerHTML = rows.map(row => `
      <div class="recent-item" data-id="${escapeHtml(row.task_id)}">
        <span class="pill ${row.status === "approved" ? "approved" : "draft"}">${escapeHtml(row.status)}</span>
        <span class="recent-title">${escapeHtml(row.title)}</span>
        <span class="muted">${row.decision_count} 决议 · ${row.action_count} 行动</span>
      </div>`).join("");
    document.querySelectorAll("#recent .recent-item").forEach(el => {
      el.onclick = () => openMeeting(el.dataset.id);
    });
  } catch (e) {
    $("recent").innerHTML = '<div class="card-b muted">加载失败：' + escapeHtml(e.message) + '</div>';
  }
}
$("document-file").onchange = async () => {
  const file = $("document-file").files[0];
  if (!file) return;
  const hint = $("upload-hint");
  const form = new FormData();
  form.append("file", file, file.name);
  hint.textContent = "上传并解析中…";
  $("document-file").disabled = true;
  try {
    const data = await api("/api/v1/office/documents/upload", { method: "POST", body: form });
    $("document").value = data.filename;
    hint.textContent = `已上传：${data.filename} · ${data.chars} 字符` +
      (data.warnings && data.warnings.length ? " · " + data.warnings.join("；") : "");
    toast("文档已上传并解析");
  } catch (e) {
    hint.textContent = "上传失败：" + e.message;
    toast("上传失败：" + e.message, true);
  } finally { $("document-file").disabled = false; }
};
$("imap-fetch").onclick = async () => {
  const hint = $("imap-hint");
  const button = $("imap-fetch");
  button.disabled = true;
  hint.textContent = "拉取中…";
  try {
    const data = await api("/api/v1/office/documents/imap", {
      method: "POST",
      body: JSON.stringify({ limit: 5, search: "ALL" })
    });
    if (!data.fetched) {
      hint.textContent = "邮箱中没有可导入的邮件";
      toast("没有可导入的邮件");
      return;
    }
    const first = data.documents[0];
    $("document").value = first.filename;
    hint.textContent = `已拉取 ${data.fetched} 封，已选：${first.subject}`;
    toast(`已拉取 ${data.fetched} 封邮件`);
  } catch (e) {
    hint.textContent = "拉取失败：" + e.message;
    toast("拉取失败：" + e.message, true);
  } finally { button.disabled = false; }
};
$("sample").onclick = () => {
  $("title").value = "产品周会 · 企业办公工作台";
  $("transcript").value = [
    "参会：王敏、李哲、陈雪、赵晨",
    "议题：会议纪要试点范围",
    "王敏：本周先做会议纪要与行动项，不做泛化办公聊天。",
    "决定：首批只覆盖会议转写、结构化纪要、行动项和 docx 导出，范围不再扩大。",
    "李哲：行动项：把纪要模板接入控制面 API，负责人：李哲，截止：2026-11-20",
    "陈雪：行动项：补 3 个真实会议样例，负责人：陈雪，截止：2026-11-18",
    "决定：所有生成内容必须带来源行号，人工批准后才能对外发送。",
    "赵晨：风险：转写质量差时行动项负责人可能缺失，先标注待确认。",
    "议题：下一步",
    "王敏：下周复盘一次，决定是否扩展到报告和表格。"
  ].join("\n");
};
$("clear").onclick = () => { $("transcript").value = ""; $("document").value = "";
  $("document-file").value = ""; $("upload-hint").textContent = ""; $("result").innerHTML =
  '<div class="result-empty"><div class="big">◎</div><div>还没有生成纪要</div></div>'; $("status").hidden = true; };
$("refresh").onclick = loadMeetings;
$("generate").onclick = async () => {
  const title = $("title").value.trim();
  const transcript = $("transcript").value.trim();
  const documentName = $("document").value.trim();
  if (!title || (!transcript && !documentName)) { toast("请填写标题和会议内容或文档", true); return; }
  const button = $("generate"); button.disabled = true; button.textContent = "生成中…";
  try {
    const body = { title, transcript, include_docx: true };
    if (documentName) body.document_filename = documentName;
    if ($("project").value) body.project_id = $("project").value;
    const data = await api("/api/v1/office/meetings", { method: "POST", body: JSON.stringify(body) });
    renderResult(data); loadMeetings(); toast("纪要已生成");
  } catch (e) { toast("生成失败：" + e.message, true); }
  finally { button.disabled = false; button.textContent = "生成纪要"; }
};
/* ---- 报告 / 表格 ---- */
let currentReport = "";
function showPane(name) {
  $("pane-meeting").hidden = name !== "meeting";
  $("pane-report").hidden = name !== "report";
  document.querySelectorAll(".tab").forEach(tab => tab.classList.toggle("active", tab.dataset.pane === name));
}
document.querySelectorAll(".tab").forEach(tab => { tab.onclick = () => showPane(tab.dataset.pane); });
function renderReport(data) {
  currentReport = data.task_id;
  const r = data.report || {};
  const artifacts = data.artifacts || [];
  $("report-status").hidden = false;
  $("report-status").textContent = data.status || "draft";
  $("report-status").className = "pill " + (data.status || "draft");
  const metrics = (r.metrics || []).map(m => {
    const ach = m.achievement == null ? "—" : (m.achievement * 100).toFixed(1) + "%";
    return `<tr><td>${escapeHtml(m.name)}</td><td>${escapeHtml(m.value)}</td>
      <td>${m.target == null ? "—" : escapeHtml(m.target)}</td><td>${ach}</td>
      <td>${escapeHtml(m.unit || "—")}</td></tr>`;
  }).join("");
  const sections = (r.sections || []).map(s =>
    `<h3>${escapeHtml(s.heading)}</h3><p class="muted" style="white-space:pre-line">${escapeHtml(s.body)}</p>`).join("");
  const has = (kind) => artifacts.some(a => a.kind === kind);
  $("report-result").innerHTML = `
    <div class="summary">${escapeHtml(r.summary || "")}</div>
    <h3>指标</h3>
    <table><thead><tr><th>指标</th><th>实际</th><th>目标</th><th>达成率</th><th>单位</th></tr></thead>
    <tbody>${metrics}</tbody></table>
    ${sections}
    <div class="actions">
      ${has("report_md") ? '<button id="r-dl-md">下载 Markdown</button>' : ""}
      ${has("report_xlsx") ? '<button id="r-dl-xlsx">下载 Excel</button>' : ""}
      ${has("report_docx") ? '<button id="r-dl-docx">下载 Word</button>' : ""}
      ${has("report_pptx") ? '<button id="r-dl-pptx">下载 PPTX</button>' : ""}
      ${has("report_pdf") ? '<button id="r-dl-pdf">下载 PDF</button>' : ""}
      ${data.status !== "approved" ? '<button class="primary" id="r-approve">批准报告</button>' : '<span class="badge ok">已批准</span>'}
    </div>`;
  const bind = (id, fn) => { const el = $(id); if (el) el.onclick = fn; };
  bind("r-dl-md", () => downloadReport(currentReport, "md"));
  bind("r-dl-xlsx", () => downloadReport(currentReport, "xlsx"));
  bind("r-dl-docx", () => downloadReport(currentReport, "docx"));
  bind("r-dl-pptx", () => downloadReport(currentReport, "pptx"));
  bind("r-dl-pdf", () => downloadReport(currentReport, "pdf"));
  bind("r-approve", approveReport);
}
async function downloadReport(taskId, format) {
  try {
    const res = await fetch(`/api/v1/office/reports/${taskId}/download?format=${format}`,
      { headers: authHeaders() });
    if (!res.ok) throw new Error(await res.text());
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "report." + format;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    toast("已下载 report." + format);
  } catch (e) { toast("下载失败：" + e.message, true); }
}
async function approveReport() {
  try {
    const data = await api(`/api/v1/office/reports/${currentReport}/approve`, { method: "POST", body: "{}" });
    renderReport(data); loadReports(); toast("报告已批准");
  } catch (e) { toast("批准失败：" + e.message, true); }
}
async function openReport(taskId) {
  try {
    const data = await api(`/api/v1/office/reports/${taskId}`);
    renderReport(data); window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (e) { toast("加载失败：" + e.message, true); }
}
async function loadReports() {
  try {
    const rows = await api("/api/v1/office/reports");
    if (!rows.length) {
      $("report-recent").innerHTML = '<div class="card-b muted">还没有报告。</div>';
      return;
    }
    $("report-recent").innerHTML = rows.map(row => `
      <div class="recent-item" data-id="${escapeHtml(row.task_id)}">
        <span class="pill ${row.status === "approved" ? "approved" : "draft"}">${escapeHtml(row.status)}</span>
        <span class="recent-title">${escapeHtml(row.title)}</span>
        <span class="muted">${row.metric_count} 项指标</span>
      </div>`).join("");
    document.querySelectorAll("#report-recent .recent-item").forEach(el => {
      el.onclick = () => openReport(el.dataset.id);
    });
  } catch (e) {
    $("report-recent").innerHTML = '<div class="card-b muted">加载失败：' + escapeHtml(e.message) + '</div>';
  }
}
$("report-sample").onclick = () => {
  $("report-title").value = "Q4 办公效率报告";
  $("report-summary").value = "本季度办公自动化覆盖率和审批效率均有提升。";
  $("report-metrics").value = [
    "会议纪要产出,42,30,份",
    "审批平均时长,1.8,2.5,天",
    "文档生成量,128,100,份",
    "人工返工率,6,10,%,lower"
  ].join("\n");
  $("report-sections").value = [
    "## 结论",
    "会议纪要与报告生成已经形成稳定链路，人工只需复核关键结论。",
    "## 风险",
    "指标口径仍需与业务方对齐，返工率样本量偏小。"
  ].join("\n");
};
$("report-clear").onclick = () => {
  $("report-metrics").value = ""; $("report-sections").value = ""; $("report-summary").value = "";
  $("report-result").innerHTML = '<div class="result-empty"><div class="big">▦</div><div>还没有生成报告</div></div>';
  $("report-status").hidden = true;
};
$("report-refresh").onclick = loadReports;
$("report-generate").onclick = async () => {
  const title = $("report-title").value.trim();
  const metrics = $("report-metrics").value.trim();
  if (!title || !metrics) { toast("请填写标题和指标", true); return; }
  const button = $("report-generate"); button.disabled = true; button.textContent = "生成中…";
  try {
    const data = await api("/api/v1/office/reports", {
      method: "POST",
      body: JSON.stringify({
        title,
        summary: $("report-summary").value.trim(),
        metrics,
        sections: $("report-sections").value.trim()
      })
    });
    renderReport(data); loadReports(); toast("报告已生成");
  } catch (e) { toast("生成失败：" + e.message, true); }
  finally { button.disabled = false; button.textContent = "生成报告"; }
};
health(); loadProjects(); loadMeetings(); loadReports();
</script>
</body>
</html>
"""
