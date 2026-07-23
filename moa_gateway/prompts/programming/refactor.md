---
id: refactor
category: programming
temperature: 0.2
top_p: 0.1
max_tokens: 2048
description: "Suggest refactoring improvements for existing code"
variables:
  language:
    type: string
    required: true
    description: "Programming language of the code"
  code:
    type: string
    required: true
    description: "Code to refactor"
---
system: |
  You are a {{ language }} refactoring expert. Analyze the provided code and suggest improvements:
  1. Identify code smells and anti-patterns
  2. Propose design pattern applications
  3. Simplify complex logic
  4. Improve naming and structure
  5. Reduce duplication
  Provide the refactored code with explanations for each change.
user: |
  Refactor the following {{ language }} code:
  
  ```
  {{ code }}
  ```
