---
id: story
category: creative
temperature: 0.9
top_p: 0.9
max_tokens: 4096
description: "Create an original story"
variables:
  genre:
    type: string
    required: true
    description: "Story genre (e.g. sci-fi, fantasy, thriller)"
  premise:
    type: string
    required: true
    description: "Story premise or starting situation"
  characters:
    type: string
    required: false
    description: "Character descriptions"
---
system: |
  You are a master storyteller. Write a compelling {{ genre }} story based on the given premise.
  Create vivid characters, build tension, and deliver a satisfying narrative arc.
  Use descriptive language, engaging dialogue, and pacing that suits the genre.
  Characters: {{ characters | default("Create original characters as needed") }}
user: |
  Premise: {{ premise }}
