import unicodedata
import re
PUNCT_RE = re.compile(
    r"[\u2000-\u206f\u2e00-\u2e7f"
    r"\u3000-\u303f"
    r"\uff00-\uffef"
    r"""'!"#$%&()*+,\-./:;<=>?@\[\\\]^_`{|}~]"""
)
s = unicodedata.normalize("NFKC", "你好，世界").lower()
print("ord of ， =", hex(ord("，")))
print("punct sub result:", repr(PUNCT_RE.sub(" ", s)))
print("after ws fold:", repr(re.sub(r"\s+", " ", PUNCT_RE.sub(" ", s)).strip()))
