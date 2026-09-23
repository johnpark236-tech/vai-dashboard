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
  
  $("btnEmergencyStop").onclick = confirmEmergencyStop;
  $("btnEmergencyClear").onclick = confirmEmergencyClear;
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
    await fetchRealOrderStatus();
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

// ==========================================
// Real Order Dashboard Integration (Phase 5.8)
// ==========================================
let realOrderStatus = null;
let pendingPrepareData = null;
let isOrderSubmitting = false;

async function fetchRealOrderStatus() {
  if (!apiToken) return;
  try {
    const res = await api("/api/real_order/status");
    realOrderStatus = await res.json();
    renderRealOrder();
  } catch (err) {
    console.warn("Real order status fetch failed:", err);
    // RULE 5: API 통신 실패 시 UNKNOWN 상태로 처리하고 주문 차단
    realOrderStatus = {
      real_order_enabled: false,
      emergency_stop: { state: "UNKNOWN", stopped: true },
      can_buy: false,
      can_sell: false,
    };
    renderRealOrder();
  }
}

function renderRealOrder() {
  if (!realOrderStatus) return;
  const ro = realOrderStatus;


  const estop = ro.emergency_stop;
  let estopState = "UNKNOWN";
  let estopStopped = true; // default safe: treat unknown as stopped

  if (estop && estop.state) {
    estopState = estop.state;
    estopStopped = estop.stopped === true;
  }

  // Show emergency stop state badge regardless of real_order_enabled
  const stopBadge = $("emergencyStopBadge");
  if (estopStopped || estopState === "UNKNOWN" || estopState === "STOP_ERROR") {
    stopBadge.style.display = "inline-block";
    stopBadge.textContent = estopState === "STOP_ERROR"
      ? "⛔ STOP_ERROR"
      : estopState === "UNKNOWN"
      ? "❓ UNKNOWN"
      : "🛑 비상정지(STOPPED)";
    $("btnEmergencyStop").style.display = "none";
    $("btnEmergencyClear").style.display = estopStopped ? "inline-block" : "none";
    if (estopState === "STOP_ERROR" && !window._stopErrorAlerted) {
      window._stopErrorAlerted = true;
      alert("⛔ 비상정지 상태 파일 오류(STOP_ERROR)가 감지되었습니다.\n신규 주문이 차단됩니다.\n관리자에게 즉시 알리십시오.");
    }
  } else {
    stopBadge.style.display = "none";
    window._stopErrorAlerted = false;
    $("btnEmergencyStop").style.display = "inline-block";
    $("btnEmergencyClear").style.display = "none";
  }

  // Show real order feature badge
  const badge = $("realOrderBadge");

  if (ro.real_order_enabled) {
    badge.textContent = "실거래 가능 (ENABLED)";
    badge.className = "badge badge-enabled";
  } else {
    badge.textContent = "기능 비활성 (DISABLED)";
    badge.className = "badge badge-disabled";
  }

  $("roPrice").textContent = won(ro.current_price);
  $("roQuoteTime").textContent = ro.quote_time_kst || "-";
  $("roOrderableCash").textContent = won(ro.orderable_cash);
  $("roHoldings").textContent = `${ro.real_holdings || 0} 주`;
  $("roSessionBuyQty").textContent = `${ro.session_buy_qty || 0} 주`;
  $("roSessionSellableQty").textContent = `${ro.session_sellable_qty || 0} 주`;

  const buyBtn = $("roBuyBtn");
  const sellBtn = $("roSellBtn");

  buyBtn.disabled = !ro.can_buy || isOrderSubmitting;
  sellBtn.disabled = !ro.can_sell || isOrderSubmitting;

  buyBtn.onclick = () => prepareOrder("BUY");
  sellBtn.onclick = () => prepareOrder("SELL");

  // Render Session Orders
  const orders = ro.orders || [];
  const tbody = $("roOrdersBody");
  if (!orders.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="text-center">체결 내역이 없습니다.</td></tr>`;
  } else {
    tbody.innerHTML = orders.map((o) => `
      <tr>
        <td>#${esc(o.order_id)}</td>
        <td><span class="pill ${o.side === 'BUY' ? 'buy' : 'sell'}">${esc(o.side)}</span></td>
        <td>${esc(o.executed_qty || o.order_qty)}주</td>
        <td>${won(o.order_price)}</td>
        <td>${won(o.executed_price)}</td>
        <td><span class="status-${(o.status || '').toLowerCase()}">${esc(o.status)}</span></td>
        <td>${esc(o.filled_at_kst || o.submitted_at_kst || '-')}</td>
        <td>${o.realized_pnl ? won(o.realized_pnl) : '-'}</td>
      </tr>
    `).join("");
  }
}

async function prepareOrder(side) {
  if (isOrderSubmitting) return;
  const statusMsg = $("roStatusMsg");
  statusMsg.textContent = "주문 조건을 검증 중입니다...";
  statusMsg.className = "ro-status-msg info";

  try {
    const curPrice = realOrderStatus?.current_price || 3330;
    const res = await fetch(`${cfg.apiBase}/api/real_order/prepare`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiToken}`
      },
      body: JSON.stringify({ side, price: curPrice })
    });

    const data = await res.json();
    if (!res.ok || !data.success) {
      statusMsg.textContent = `주문 준비 실패: ${data.error || "알 수 없는 오류"}`;
      statusMsg.className = "ro-status-msg error";
      return;
    }

    pendingPrepareData = data;
    showConfirmModal(data);
    statusMsg.textContent = "";
  } catch (err) {
    statusMsg.textContent = `네트워크 오류: ${err.message}`;
    statusMsg.className = "ro-status-msg error";
  }
}

function showConfirmModal(data) {
  $("modalSide").textContent = data.side === "BUY" ? "매수 (BUY)" : "매도 (SELL)";
  $("modalPrice").textContent = won(data.expected_price);
  $("modalTotal").textContent = data.side === "BUY" ? won(data.total_needed) : `${won(data.expected_price)} (예상 수령: ${won(data.expected_price - data.fee_estimate - data.tax_estimate)})`;
  
  const modal = $("roConfirmModal");
  modal.hidden = false;

  $("modalConfirmBtn").onclick = submitConfirmedOrder;
  $("modalCancelBtn").onclick = () => {
    modal.hidden = true;
    pendingPrepareData = null;
  };
}

async function submitConfirmedOrder() {
  if (!pendingPrepareData || isOrderSubmitting) return;
  isOrderSubmitting = true;
  $("modalConfirmBtn").disabled = true;
  $("modalConfirmBtn").textContent = "주문 전송 중...";

  const statusMsg = $("roStatusMsg");
  statusMsg.textContent = "실제 증권사 주문을 전송 중입니다. 잠시 기다려 주십시오...";
  statusMsg.className = "ro-status-msg info";

  try {
    const res = await fetch(`${cfg.apiBase}/api/real_order/submit`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiToken}`
      },
      body: JSON.stringify({
        nonce: pendingPrepareData.nonce,
        side: pendingPrepareData.side,
        order_type: "LIMIT"
      })
    });

    const data = await res.json();
    $("roConfirmModal").hidden = true;

    if (!res.ok || !data.success) {
      statusMsg.textContent = `주문 실패: ${data.error || "체결 오류"}`;
      statusMsg.className = "ro-status-msg error";
    } else {
      statusMsg.textContent = `✅ ${data.side === 'BUY' ? '매수' : '매도'} 주문이 성공적으로 체결되었습니다! (단가: ${won(data.executed_price)})`;
      statusMsg.className = "ro-status-msg success";
    }
  } catch (err) {
    $("roConfirmModal").hidden = true;
    statusMsg.textContent = `주문 전송 실패: ${err.message}`;
    statusMsg.className = "ro-status-msg error";
  } finally {
    isOrderSubmitting = false;
    $("modalConfirmBtn").disabled = false;
    $("modalConfirmBtn").textContent = "최종 주문 전송";
    pendingPrepareData = null;
    await fetchRealOrderStatus();
  }
}




async function confirmEmergencyStop() {
  const pin = prompt("🚨 비상정지를 작동하시겠습니까?

신규 주문 접수가 즉시 차단됩니다.
6자리 비상정지 PIN을 입력하세요:");
  if (!pin) return;
  try {
    const res = await fetch(`${cfg.apiBase}/api/real_order/emergency_stop`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${apiToken}`, "Content-Type": "application/json" },
      body: JSON.stringify({ pin: pin })
    });
    const data = await res.json();
    if (res.ok && data.success) {
      alert("비상정지가 성공적으로 작동되었습니다.");
      await fetchRealOrderStatus();
    } else {
      alert(`비상정지 실패: ${data.error} - ${data.message || ''}`);
    }
  } catch (err) {
    alert(`비상정지 요청 실패: ${err.message}`);
  }
}

async function confirmEmergencyClear() {
  const pin = prompt("✅ 비상정지를 해제하시겠습니까?

정지 해제 후에는 다시 주문이 가능해집니다.
6자리 비상정지 PIN을 입력하세요:");
  if (!pin) return;
  try {
    const res = await fetch(`${cfg.apiBase}/api/real_order/emergency_stop_clear`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${apiToken}`, "Content-Type": "application/json" },
      body: JSON.stringify({ pin: pin })
    });
    const data = await res.json();
    if (res.ok && data.success) {
      alert("비상정지가 성공적으로 해제되었습니다.");
      await fetchRealOrderStatus();
    } else {
      alert(`비상정지 해제 실패: ${data.error} - ${data.message || ''}`);
    }
  } catch (err) {
    alert(`비상정지 해제 요청 실패: ${err.message}`);
  }
}
