---
id: code_generation
category: programming
temperature: 0.2
top_p: 0.1
max_tokens: 2048
description: "Generate code in a specified programming language"
variables:
  language:
    type: string
    required: true
    description: "Target programming language"
  task:
    type: string
    required: true
    description: "Coding task description"
---
system: |
  You are an expert {{ language }} programmer. Write clean, well-documented code following best practices.
  Always include error handling and type annotations where applicable.
  Return only the code with a brief explanation.
user: |
  {{ task }}
