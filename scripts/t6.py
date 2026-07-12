from pathlib import Path
fp = 'D:/MoA Gateway Pro/scripts/audit_ui_e2e.py'
text = Path(fp).read_text(encoding='utf-8')

old = '''            new_ep = {
                \"id\": \"test_ep_audit\", \"provider\": \"mock\", \"model\": \"test\", \"tier\": \"lite\",
                \"api_key\": \"\", \"enabled\": True, \"weight\": 50, \"max_tokens\": 4096,
                \"cost_per_1k_input\": 0.0001, \"cost_per_1k_output\": 0.0001,
                \"tags\": [\"audit-test\"],
            }'''
new = '''            new_ep = {
                \"endpoint_id\": \"test_ep_audit\", \"provider\": \"mock\", \"model\": \"test\",
                \"tier\": \"lite\", \"api_key_plain\": \"\", \"enabled\": True, \"weight\": 50,
                \"max_tokens\": 4096, \"cost_per_1k_input\": 0.0001,
                \"cost_per_1k_output\": 0.0001, \"tags\": [\"audit-test\"],
            }'''
assert old in text, 'not found'
text = text.replace(old, new)
Path(fp).write_text(text, encoding='utf-8')
print('OK')
