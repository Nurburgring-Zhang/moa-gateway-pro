from pathlib import Path
fp = 'D:/MoA Gateway Pro/moa_gateway/ui/pages2.py'
text = Path(fp).read_text(encoding='utf-8')

# 5 个多行 http_post + 1 个 change-password
replacements = [
    ('\"category\": category, \"limit\": limit}, timeout=300)',
     '\"category\": category, \"limit\": limit}, timeout=300, token=sr.admin_token)'),
    ('{\"prompts\": prompts, \"presets\": presets}, timeout=300)',
     '{\"prompts\": prompts, \"presets\": presets}, timeout=300, token=sr.admin_token)'),
    ('\"preset\": preset, \"max_tokens\": max_tokens_e.value}, timeout=180, token=sr.admin_token)',
     '\"preset\": preset, \"max_tokens\": max_tokens_e.value}, timeout=180, token=sr.admin_token)'),
    ('\"preset\": preset_dd.value or \"balanced\"}, timeout=180)',
     '\"preset\": preset_dd.value or \"balanced\"}, timeout=180, token=sr.admin_token)'),
    ('\"response\": flask_response.value, \"reference\": flask_ref.value or None}, timeout=60)',
     '\"response\": flask_response.value, \"reference\": flask_ref.value or None}, timeout=60, token=sr.admin_token)'),
    ('{\"old_password\": old_e.value, \"new_password\": new_e.value})',
     '{\"old_password\": old_e.value, \"new_password\": new_e.value}, token=sr.admin_token)'),
]

count = 0
for old, new in replacements:
    if old == new:
        continue
    if old in text:
        text = text.replace(old, new, 1)
        count += 1
        print('  +1:', old[:60])
    else:
        print('  NOT FOUND:', old[:60])

Path(fp).write_text(text, encoding='utf-8')
print('OK, ' + str(count) + ' replaced')
