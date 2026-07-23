---
id: creative_writing
category: writing
temperature: 0.7
top_p: 0.8
max_tokens: 4096
description: "Creative writing with customizable genre, tone, and topic"
variables:
  genre:
    type: string
    required: true
    description: "Literary genre (e.g. fiction, poetry, script)"
  topic:
    type: string
    required: true
    description: "Subject or theme to write about"
  tone:
    type: string
    required: false
    description: "Desired tone (e.g. melancholic, humorous, suspenseful)"
---
system: |
  You are a versatile creative writer. Write engaging {{ genre }} with a {{ tone | default("neutral") }} tone.
  Use vivid imagery, compelling narrative voice, and thoughtful structure.
  Aim for literary quality while remaining accessible.
user: |
  Write a {{ genre }} piece about: {{ topic }}
