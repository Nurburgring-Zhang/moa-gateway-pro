---
id: meeting_notes
category: summarization
temperature: 0.3
top_p: 0.3
max_tokens: 1024
description: "Generate structured meeting notes from transcript"
variables:
  transcript:
    type: string
    required: true
    description: "Meeting transcript or notes"
  format:
    type: string
    required: false
    description: "Output format (bullet, structured, action-items)"
---
system: |
  You are a meeting notes specialist. Create {{ format | default("structured") }} meeting notes:
  1. List attendees and meeting purpose
  2. Summarize key discussions by topic
  3. Record decisions made
  4. List action items with owners and deadlines
  5. Note any unresolved questions or follow-ups
  Be concise but comprehensive.
user: |
  Meeting transcript:
  
  {{ transcript }}
