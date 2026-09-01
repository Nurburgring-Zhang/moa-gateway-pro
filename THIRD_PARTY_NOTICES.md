# Third-Party Notices — MOA Gateway Pro v4.1.0

MOA Gateway Pro v4.1.0 integrates capabilities, techniques, and data ported from
three open-source projects. All three are distributed under the MIT License.
Their original copyright notices and license texts are reproduced below, as
required by the MIT License terms.

Ported components per project are listed in `RELEASE_NOTES_v4.1.md`.

---

## OmniRoute

- Repository: https://github.com/diegosouzapw/OmniRoute
- License: MIT
- Integrated as: routing strategy engine (20 strategies), quota telemetry and
  quota-aware scheduling (QuotaValue, adaptive monitor, DRR + P2C quota-share),
  RTK + Caveman stacked compression with fidelity gate, free-tier model catalog
  (456 entries), and the A2A agent-card / JSON-RPC 2.0 protocol layer.

```
MIT License

Copyright (c) 2026 diegosouzapw

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## OpenClacky

- Repository: https://github.com/clacky-ai/openclacky
- License: MIT
- Integrated as: token-efficiency toolkit (double ephemeral cache markers,
  immutable system prompt + side-channel injection, Insert-then-Compress,
  idle compression scheduling), SKILL.md skill ecosystem (loader, discovery,
  fuzzy search, invoke_skill meta-tool, natural-language creation,
  self-evolution hooks), IM channel adapter layer (Telegram / Feishu /
  DingTalk / WeCom / Discord), and lite-model subagent routing.

```
The MIT License (MIT)

Copyright (c) 2025 windy

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

---

## MemoraX Code

- Repository: https://github.com/memorax-ai/memorax-code
- License: MIT
- Integrated as: cross-session memory layer (five memory types, scope model,
  fail-closed hook protocol, hybrid dense+sparse recall, redaction →
  buffering → chunked idempotent writeback pipeline) and workspace/repo
  memory (facets, adaptive update policy, supervisor lock).

```
MIT License

Copyright (c) 2026 MemoraX AI

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
