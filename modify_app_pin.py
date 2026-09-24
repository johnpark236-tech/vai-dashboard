import re

with open('app.js', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace confirmEmergencyStop and clear
new_funcs = '''
async function confirmEmergencyStop() {
  const pin = prompt("🚨 비상정지를 작동하시겠습니까?\\n\\n신규 주문 접수가 즉시 차단됩니다.\\n6자리 비상정지 PIN을 입력하세요:");
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
  const pin = prompt("✅ 비상정지를 해제하시겠습니까?\\n\\n정지 해제 후에는 다시 주문이 가능해집니다.\\n6자리 비상정지 PIN을 입력하세요:");
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
'''

content = re.sub(r'async function confirmEmergencyStop\(\) \{.*?\}\s*async function confirmEmergencyClear\(\) \{.*?\}', new_funcs, content, flags=re.DOTALL)

with open('app.js', 'w', encoding='utf-8', newline='\n') as f:
    f.write(content)
