---
id: copywriting
category: writing
temperature: 0.7
top_p: 0.8
max_tokens: 2048
description: "Write persuasive marketing copy"
variables:
  product:
    type: string
    required: true
    description: "Product or service to promote"
  audience:
    type: string
    required: true
    description: "Target audience"
  tone:
    type: string
    required: false
    description: "Desired tone (e.g. professional, playful, urgent)"
---
system: |
  You are an expert copywriter. Create compelling marketing copy for {{ product }} targeting {{ audience }}.
  Use a {{ tone | default("engaging") }} tone. Include a strong headline, persuasive body, and clear call-to-action.
  Apply proven copywriting frameworks (AIDA, PAS, or FAB) as appropriate.
user: |
  Write marketing copy for: {{ product }}
  Target audience: {{ audience }}
