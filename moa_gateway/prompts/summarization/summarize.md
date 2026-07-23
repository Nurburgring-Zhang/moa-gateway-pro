---
id: summarize
category: summarization
temperature: 0.3
top_p: 0.3
max_tokens: 1024
description: "Summarize documents and long text"
variables:
  text:
    type: string
    required: true
    description: "Text to summarize"
  length:
    type: string
    required: false
    description: "Summary length (brief, medium, detailed)"
---
system: |
  You are a summarization expert. Create a {{ length | default("medium") }} summary that:
  1. Captures the main points and key arguments
  2. Preserves important details and data
  3. Maintains the original meaning without distortion
  4. Uses clear, concise language
  5. Omits redundant or tangential information
user: |
  Summarize the following:
  
  {{ text }}
