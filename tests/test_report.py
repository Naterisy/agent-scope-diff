import copy
import unittest

from report import render


def fixture():
    root = "/home/private-account/myrepo"
    left = {
        "cwd": root + "/<script>",
        "root": root,
        "used_bytes": 3,
        "budget": 100,
        "files": [
            {"scope": "project", "path": root + "/<script>/AGENTS.md",
             "status": "picked", "included_bytes": 3, "digest": "PRIVATE_DIGEST"},
            {"scope": "global", "path": "/private/account/AGENTS.md",
             "status": "picked", "included_bytes": 2, "digest": "GLOBAL_DIGEST"},
        ],
        "warnings": ["/private/this-should-not-be-shown"],
    }
    right = copy.deepcopy(left)
    right["cwd"] = root
    right["files"].pop(0)
    return {"schema_version": 1, "mode": "compare", "left": left, "right": right}


class ReportTests(unittest.TestCase):
    def test_html_escapes_paths_and_does_not_render_absolute_paths_or_digests(self):
        text = render(fixture())
        self.assertIn("&lt;script&gt;/AGENTS.md", text)
        self.assertIn("$CODEX_HOME/AGENTS.md", text)
        for private in ("private-account", "/private/", "PRIVATE_DIGEST", "GLOBAL_DIGEST", "<script>"):
            self.assertNotIn(private, text)

    def test_comparison_reports_row_difference(self):
        text = render(fixture())
        self.assertIn("<strong>1</strong>", text)
        self.assertIn("Not in scope", text)

    def test_other_schemas_are_rejected(self):
        with self.assertRaises(ValueError):
            render({"schema_version": 2, "mode": "compare"})
