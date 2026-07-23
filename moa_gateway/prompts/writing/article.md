---
id: article
category: writing
temperature: 0.7
top_p: 0.8
max_tokens: 4096
description: "Write a structured article on a given topic"
variables:
  topic:
    type: string
    required: true
    description: "Article topic"
  style:
    type: string
    required: false
    description: "Writing style (e.g. academic, journalistic, blog)"
  length:
    type: string
    required: false
    description: "Desired length (e.g. short, medium, long)"
---
system: |
  You are a skilled article writer. Write a well-structured article in a {{ style | default("informative") }} style.
  Include a compelling introduction, clear body paragraphs with evidence, and a strong conclusion.
  Target length: {{ length | default("medium") }}.
user: |
  Write an article about: {{ topic }}
