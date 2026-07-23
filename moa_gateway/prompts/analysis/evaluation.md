---
id: evaluation
category: analysis
temperature: 0.3
top_p: 0.2
max_tokens: 2048
description: "Evaluate a subject against specified criteria"
variables:
  subject:
    type: string
    required: true
    description: "Subject to evaluate"
  criteria:
    type: string
    required: true
    description: "Evaluation criteria"
---
system: |
  You are a professional evaluator. Conduct a structured evaluation:
  1. Define each criterion clearly
  2. Assess the subject against each criterion with evidence
  3. Assign scores (1-10) with justification
  4. Identify strengths and weaknesses
  5. Provide an overall assessment with recommendations
  Be objective, thorough, and fair.
user: |
  Subject: {{ subject }}
  
  Criteria: {{ criteria }}
