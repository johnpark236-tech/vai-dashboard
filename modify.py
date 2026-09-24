import re

with open('index.html', 'r', encoding='utf-8') as f:
    content = f.read()

replacement = '''
          <div>
            <span id="emergencyStopBadge" class="badge" style="background:red;color:white;display:none;">비상정지(STOPPED)</span>
            <span id="realOrderBadge" class="badge badge-disabled">기능 비활성 (DISABLED)</span>
          </div>
        </div>
        <div class="emergency-controls" style="margin-bottom:15px;padding:10px;border:1px solid #ff4d4f;border-radius:4px;background:#fff1f0;">
          <button id="btnEmergencyStop" class="btn" style="background:#cf1322;color:white;cursor:pointer;">🚨 비상정지 작동 (STOP)</button>
          <button id="btnEmergencyClear" class="btn" style="display:none;background:#52c41a;color:white;cursor:pointer;">비상정지 해제</button>
        </div>
        <div style="display:none;">
'''

content = content.replace('<span id="realOrderBadge" class="badge badge-disabled">기능 비활성 (DISABLED)</span>\n          </div>', replacement)

with open('index.html', 'w', encoding='utf-8', newline='\n') as f:
    f.write(content)
