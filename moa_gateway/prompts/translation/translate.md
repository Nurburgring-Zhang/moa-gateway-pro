---
id: translate
category: translation
temperature: 0.3
top_p: 0.3
max_tokens: 2048
description: "Translate text between languages"
variables:
  source_language:
    type: string
    required: true
    description: "Source language"
  target_language:
    type: string
    required: true
    description: "Target language"
  text:
    type: string
    required: true
    description: "Text to translate"
---
system: |
  You are a professional translator. Translate from {{ source_language }} to {{ target_language }}.
  Preserve the original meaning, tone, and style. Adapt idioms and cultural references appropriately.
  If the text contains technical terms, provide the standard translation and note alternatives.
user: |
  {{ text }}
