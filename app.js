const cfg = window.VAI_CONFIG || {};
let apiToken = "";
let lastSnapshot = null;

const $ = (id) => document.getElementById(id);
const won = (v) => `${Number(v || 0).toLocaleString("ko-KR")}원`;
const pct = (v) => `${Number(v || 0).toFixed(3)}%`;
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function configured() {
  return cfg.apiBase && cfg.googleClientId && !cfg.googleClientId.startsWith("CONFIGURE_");
}

window.onload = () => {
  $("logoutBtn").onclick = logout;
  $("csvBtn").onclick = downloadCsv;
  $("filterText").oninput = renderTrades;
  if (!configured()) {
    $("configStatus").textContent = "Google OAuth Client ID 설정이 필요합니다. 현재 화면은 공개 정적 배포 검증용입니다.";
    return;
  }
  $("configStatus").textContent = "허용된 Google 계정으로 로그인하십시오.";
  google.accounts.id.initialize({ client_id: cfg.googleClientId, callback: onGoogleCredential });
  google.accounts.id.renderButton(document.querySelector(".g_id_signin"), { theme: "outline", size: "large" });
};

async function onGoogleCredential(response) {
  try {
    $("configStatus").textContent = "Google 인증 진행 중입니다...";
    const res = await fetch(`${cfg.apiBase}/api/auth/google`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ credential: response.credential })
    });
    if (!res.ok) {
      let errMsg = `로그인 차단: ${res.status}`;
      try {
        const errData = await res.json();
        if (errData && errData.error) errMsg += ` (${errData.error})`;
      } catch (_) {}
      $("configStatus").textContent = errMsg;
      return;
    }
    const body = await res.json();
    apiToken = body.access_token;
    $("loginPanel").hidden = true;
    $("dashboard").hidden = false;
    $("logoutBtn").hidden = false;
    await refresh();
    setInterval(refresh, Number(cfg.refreshMs || 10000));
  } catch (err) {
    console.error("Login failure:", err);
    $("configStatus").textContent = `서버 연결 실패: ${err.message || "네트워크 오류"}`;
  }
}

function logout() {
  apiToken = "";
  lastSnapshot = null;
  $("dashboard").hidden = true;
  $("logoutBtn").hidden = true;
  $("loginPanel").hidden = false;
  $("configStatus").textContent = "로그아웃되었습니다.";
}

async function api(path) {
  const res = await fetch(`${cfg.apiBase}${path}`, { headers: { Authorization: `Bearer ${apiToken}` } });
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res;
}

async function refresh() {
  try {
    const res = await api("/api/dashboard");
    lastSnapshot = await res.json();
    render();
  } catch (err) {
    console.error("Dashboard refresh failure:", err);
  }
}

function render() {
  const s = lastSnapshot.summary || {};
  const cards = [
    ["전체 가상자산", won(s.total_asset)],
    ["총 가상 현금", won(s.total_cash)],
    ["보유 주식 평가액", won(s.stock_valuation)],
    ["누적 실현손익", won(s.realized_pnl)],
    ["미실현손익", won(s.unrealized_pnl)],
    ["전체 수익률", pct(s.return_pct)],
    ["오늘 매수", `${s.today_buys || 0}건`],
    ["오늘 매도", `${s.today_sells || 0}건`],
    ["마지막 시세", s.last_quote_at || "데이터 없음"],
    ["마지막 백업", s.last_backup_at || "데이터 없음"],
    ["실제 주문", "0건"]
  ];
  $("summaryGrid").innerHTML = cards.map(([k, v]) => `<section class="metric"><span>${esc(k)}</span><strong>${esc(v)}</strong></section>`).join("");
  renderAccounts();
  renderHourly();
  renderTrades();
  renderServices();
  drawBars("pnlChart", lastSnapshot.chart?.daily_pnl || [], "date", "value", "#1467b3");
  drawBars("returnChart", lastSnapshot.chart?.account_returns || [], "account", "value", "#0b7d45");
}

function renderAccounts() {
  const rows = lastSnapshot.accounts || [];
  $("accountsTable").innerHTML = `<thead><tr><th>계좌</th><th>전략</th><th>구분</th><th>현금</th><th>종목</th><th>수량</th><th>매수가</th><th>평가액</th><th>실현손익</th><th>수익률</th></tr></thead><tbody>${rows.map((a) => {
    const p = a.position || {};
    return `<tr><td>${esc(a.account_id)}</td><td>${esc(a.mode)}</td><td>가상</td><td>${won(a.cash)}</td><td>${esc(p.symbol || "-")}</td><td>${esc(p.quantity || 0)}</td><td>${won(p.entry_price)}</td><td>${won(a.valuation)}</td><td>${won(a.realized_pnl)}</td><td>${pct(a.return_pct)}</td></tr>`;
  }).join("") || `<tr><td colspan="10">데이터 없음</td></tr>`}</tbody>`;
}

function renderHourly() {
  const h = lastSnapshot.hourly || {};
  $("hourlyPanel").innerHTML = `<p><b>${esc(h.name || "한온시스템")} ${esc(h.symbol || "018880")}</b></p><p>다음 예정: ${esc(h.next_event || "데이터 없음")}</p><p>오늘 BUY ${h.today_buys || 0}건 · SELL ${h.today_sells || 0}건</p><p>Telegram 성공 ${h.telegram_sent_count || 0}건 · 실패 ${h.telegram_failed_count || 0}건</p>`;
  const trades = h.trades || [];
  $("timeline").innerHTML = Array.from({ length: 6 }, (_, i) => {
    const hour = 9 + i;
    const side = hour % 2 ? "BUY" : "SELL";
    const t = trades.find((x) => String(x.executed_at_kst || "").slice(11, 13) === String(hour).padStart(2, "0"));
    return `<div class="timeline-item ${side.toLowerCase()}"><b>${String(hour).padStart(2, "0")}:10 ${side}</b><span>${t ? "체결" : "거래 없음/보류"}</span><small>${t ? `${won(t.price)} · ${won(t.realized_pnl)}` : ""}</small></div>`;
  }).join("");
  $("opsPanel").innerHTML = `<p>마지막 백업: ${esc(lastSnapshot.summary?.last_backup_at || "데이터 없음")}</p><p>Telegram 성공 ${h.telegram_sent_count || 0}건 · 실패 ${h.telegram_failed_count || 0}건</p>`;
}

function renderTrades() {
  const q = ($("filterText").value || "").toLowerCase();
  const rows = (lastSnapshot?.recent_trades || []).filter((t) => JSON.stringify(t).toLowerCase().includes(q));
  $("tradesTable").innerHTML = `<thead><tr><th>시각</th><th>종목</th><th>계좌</th><th>전략</th><th>구분</th><th>수량</th><th>가격</th><th>거래금액</th><th>수수료</th><th>세금</th><th>실현손익</th></tr></thead><tbody>${rows.map((t) => `<tr><td>${esc(String(t.executed_at_kst || "").slice(0, 19))}</td><td>${esc(t.symbol)}</td><td>${esc(t.account_id)}</td><td>${esc(t.mode)}</td><td>${esc(t.side)}</td><td>${esc(t.quantity)}</td><td>${won(t.price)}</td><td>${won(t.gross_amount)}</td><td>${won(t.fee)}</td><td>${won(t.tax)}</td><td>${won(t.realized_pnl)}</td></tr>`).join("") || `<tr><td colspan="11">거래 없음</td></tr>`}</tbody>`;
}

function renderServices() {
  $("services").innerHTML = (lastSnapshot.services || []).map((s) => `<li><b>${esc(s.name)}</b><span class="pill ${esc(s.state)}">${esc(s.state)}</span></li>`).join("");
}

async function downloadCsv() {
  const res = await api("/api/trades.csv");
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "vai_trades.csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

function drawBars(id, rows, labelKey, valueKey, color) {
  const c = $(id);
  const ctx = c.getContext("2d");
  const ratio = devicePixelRatio || 1;
  const w = c.width = c.clientWidth * ratio;
  const h = c.height = c.clientHeight * ratio;
  ctx.scale(ratio, ratio);
  const W = w / ratio, H = h / ratio, p = 28;
  const vals = rows.map((r) => Number(r[valueKey] || 0));
  const max = Math.max(1, ...vals.map(Math.abs));
  ctx.font = "12px Arial";
  ctx.fillStyle = "#66727f";
  ctx.fillText(rows.length ? rows.map((r) => r[labelKey]).join(" · ") : "데이터 없음", p, 18);
  rows.forEach((r, i) => {
    const bw = (W - p * 2) / Math.max(rows.length, 1) - 8;
    const x = p + i * (bw + 8);
    const v = Number(r[valueKey] || 0);
    const bh = Math.abs(v) / max * (H - 60);
    ctx.fillStyle = v < 0 ? "#b42318" : color;
    ctx.fillRect(x, H - 30 - bh, bw, bh);
    ctx.fillStyle = "#17202a";
    ctx.fillText(v.toFixed(1), x, H - 10);
  });
}
