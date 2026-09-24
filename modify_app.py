import re

with open('app.js', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add event listeners
onload_injection = '''
  $("btnEmergencyStop").onclick = confirmEmergencyStop;
  $("btnEmergencyClear").onclick = confirmEmergencyClear;
'''
content = content.replace('google.accounts.id.renderButton(', onload_injection + '  google.accounts.id.renderButton(')


# 2. Add render logic inside renderRealOrder
render_injection = '''
  const estop = ro.emergency_stop;
  if (estop && estop.stopped) {
    $("emergencyStopBadge").style.display = "inline-block";
    $("btnEmergencyStop").style.display = "none";
    $("btnEmergencyClear").style.display = "inline-block";
  } else {
    $("emergencyStopBadge").style.display = "none";
    $("btnEmergencyStop").style.display = "inline-block";
    $("btnEmergencyClear").style.display = "none";
  }

  const badge = $("realOrderBadge");
'''
content = content.replace('  const badge = $("realOrderBadge");', render_injection)

# 3. Add emergency API functions
api_functions = '''
async function confirmEmergencyStop() {
  if (!confirm("🚨 비상정지를 작동하시겠습니까?\\n\\n신규 주문 접수가 즉시 차단됩니다. 진행하시겠습니까?")) return;
  try {
    const res = await fetch(`${cfg.apiBase}/api/real_order/emergency_stop`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${apiToken}`, "Content-Type": "application/json" },
      body: "{}"
    });
    if (!res.ok) throw new Error(`API Error: ${res.status}`);
    const data = await res.json();
    if (data.success) {
      alert("비상정지가 성공적으로 작동되었습니다.");
      await fetchRealOrderStatus();
    } else {
      alert(`비상정지 실패: ${data.error}`);
    }
  } catch (err) {
    alert(`비상정지 요청 실패: ${err.message}`);
  }
}

async function confirmEmergencyClear() {
  if (!confirm("✅ 비상정지를 해제하시겠습니까?\\n\\n정지 해제 후에는 다시 주문이 가능해집니다.")) return;
  // TODO: 관리자 재인증 추가 가능
  try {
    const res = await fetch(`${cfg.apiBase}/api/real_order/emergency_stop_clear`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${apiToken}`, "Content-Type": "application/json" },
      body: "{}"
    });
    if (!res.ok) throw new Error(`API Error: ${res.status}`);
    const data = await res.json();
    if (data.success) {
      alert("비상정지가 성공적으로 해제되었습니다.");
      await fetchRealOrderStatus();
    } else {
      alert(`비상정지 해제 실패: ${data.error}`);
    }
  } catch (err) {
    alert(`비상정지 해제 요청 실패: ${err.message}`);
  }
}
'''
content += "\n" + api_functions

with open('app.js', 'w', encoding='utf-8', newline='\n') as f:
    f.write(content)
