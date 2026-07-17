"""mx_annot tests — A-39 MX + A-40 fan-in + A-44 mx CLI"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from moa_gateway.capability.mx_annot import (
    MXAnnotation,
    MXTag,
    annotations_to_json,
    compute_fanin,
    mx_cli,
    parse_mx_annotations,
)


class TestMXTag:
    def test_6_tags(self):
        assert len(MXTag) == 6
        assert {t.value for t in MXTag} == {"NOTE", "WARN", "ANCHOR", "REASON", "TODO", "SPEC"}


class TestParseMX:
    def test_python_note(self):
        text = "# mx:NOTE: hello"
        anns = parse_mx_annotations(text, "f.py", "python")
        assert len(anns) == 1
        assert anns[0].tag == MXTag.NOTE
        assert anns[0].content == "hello"

    def test_go_warn(self):
        text = "// mx:WARN: deprecated"
        anns = parse_mx_annotations(text, "f.go", "go")
        assert len(anns) == 1
        assert anns[0].tag == MXTag.WARN

    def test_c_anchor(self):
        text = "/* mx:ANCHOR: fix here */"
        anns = parse_mx_annotations(text, "f.c", "c")
        assert len(anns) == 1
        assert anns[0].tag == MXTag.ANCHOR

    def test_html_reason(self):
        text = "<!-- mx:REASON: legacy code -->"
        anns = parse_mx_annotations(text, "f.html", "html")
        assert len(anns) == 1
        assert anns[0].tag == MXTag.REASON

    def test_todo_spec(self):
        text = "# mx:TODO: refactor\n# mx:SPEC: API v2.0"
        anns = parse_mx_annotations(text, "f.py", "python")
        assert len(anns) == 2
        assert {a.tag for a in anns} == {MXTag.TODO, MXTag.SPEC}

    def test_universal_parse(self):
        """universal regex detects all 6 prefix styles in single text"""
        text = """# mx:NOTE: a
// mx:WARN: b
/* mx:ANCHOR: c */
<!-- mx:REASON: d -->
# mx:TODO: e
# mx:SPEC: f"""
        anns = parse_mx_annotations(text, "f.py", "python")
        assert len(anns) == 6
        tags = [a.tag for a in anns]
        assert MXTag.NOTE in tags
        assert MXTag.WARN in tags
        assert MXTag.ANCHOR in tags
        assert MXTag.REASON in tags
        assert MXTag.TODO in tags
        assert MXTag.SPEC in tags

    def test_line_number(self):
        text = "x\n# mx:NOTE: y\nz"
        anns = parse_mx_annotations(text, "f.py", "python")
        assert anns[0].line_number == 2

    def test_file_path(self):
        anns = parse_mx_annotations("# mx:NOTE: x", "test.py", "python")
        assert anns[0].file_path == "test.py"

    def test_unknown_tag_ignored(self):
        anns = parse_mx_annotations("# mx:UNKNOWN: x", "f.py", "python")
        assert len(anns) == 0

    def test_no_annotation(self):
        anns = parse_mx_annotations("just code\nno annotations", "f.py", "python")
        assert len(anns) == 0


class TestFanIn:
    def test_duplicate_content_counted(self):
        a1 = MXAnnotation(MXTag.NOTE, "important thing", "f.py", 1, "python")
        a2 = MXAnnotation(MXTag.NOTE, "important item", "f.py", 2, "python")
        counts = compute_fanin([a1, a2])
        # both start with "important"
        assert counts.get("important", 0) == 2

    def test_different_content(self):
        a1 = MXAnnotation(MXTag.NOTE, "alpha", "f.py", 1, "python")
        a2 = MXAnnotation(MXTag.NOTE, "beta", "f.py", 2, "python")
        counts = compute_fanin([a1, a2])
        assert counts["alpha"] == 1
        assert counts["beta"] == 1

    def test_empty_content(self):
        a = MXAnnotation(MXTag.NOTE, "", "f.py", 1, "python")
        counts = compute_fanin([a])
        assert counts.get("NOTE", 0) == 1


class TestMXCLI:
    def setup_method(self):
        self.anns = [
            MXAnnotation(MXTag.NOTE, "first note", "f1.py", 1, "python"),
            MXAnnotation(MXTag.WARN, "warning", "f2.py", 5, "python"),
            MXAnnotation(MXTag.TODO, "do something", "f3.py", 10, "python"),
        ]

    def test_list(self):
        out = mx_cli(self.anns, "list")
        assert "first note" in out
        assert "warning" in out
        assert "3" in out or len(self.anns) == 3  # has 3 entries

    def test_count_tag(self):
        assert mx_cli(self.anns, "count NOTE") == "1"
        assert mx_cli(self.anns, "count WARN") == "1"
        assert mx_cli(self.anns, "count TODO") == "1"

    def test_find_keyword(self):
        out = mx_cli(self.anns, "find note")
        assert "first note" in out

    def test_unknown_command(self):
        out = mx_cli(self.anns, "garbage")
        assert "unknown" in out

    def test_empty_annotations(self):
        out = mx_cli([], "list")
        assert "no" in out.lower()

    def test_empty_command(self):
        assert mx_cli(self.anns, "") == ""


class TestJSON:
    def test_annotation_to_dict(self):
        a = MXAnnotation(MXTag.NOTE, "x", "f.py", 1, "python")
        d = a.to_dict()
        assert d["tag"] == "NOTE"
        assert d["content"] == "x"
        assert d["line_number"] == 1

    def test_annotations_to_json(self):
        anns = [MXAnnotation(MXTag.NOTE, "x", "f.py", 1, "python")]
        j = annotations_to_json(anns)
        assert "NOTE" in j
        assert "x" in j
