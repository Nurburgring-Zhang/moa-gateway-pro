import capability.input_fingerprint as m
print("pattern:", m._PUNCT_RE.pattern)
print("ff0c match?", m._PUNCT_RE.search("\uff0c"))
print("ff0c sub:", m._PUNCT_RE.sub(" ", "你好\uff0c世界"))
