from pathlib import Path
fp = 'D:/MoA Gateway Pro/moa_gateway/ui/pages2.py'
text = Path(fp).read_text(encoding='utf-8')

# chat completions (line 283)
old1 = '\"preset\": preset,\n                }, timeout=120)'
new1 = '\"preset\": preset,\n                }, timeout=120, token=sr.admin_token)'
if old1 in text:
    text = text.replace(old1, new1, 1)
    print('+1 chat completions')

# flask (line 350)
old2 = '\"query\": flask_query.value, \"response\": flask_response.value,\n                }, timeout=120)'
new2 = '\"query\": flask_query.value, \"response\": flask_response.value,\n                }, timeout=120, token=sr.admin_token)'
if old2 in text:
    text = text.replace(old2, new2, 1)
    print('+1 flask')

Path(fp).write_text(text, encoding='utf-8')
print('OK')
