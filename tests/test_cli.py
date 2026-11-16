"""Black-box contract tests; every read is confined to a synthetic fixture.

Discovery expectations follow openai/codex agents_md.rs (2026-11-16).
"""
import json
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

CLI = Path(__file__).resolve().parents[1] / 'agent_scope_diff.py'


class ScopeDiffCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        (self.repo / '.git').mkdir()
        self.home = self.base / 'home'
        self.home.mkdir()

    def write(self, relative, text, parent=None):
        path = (parent or self.repo) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def run_cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(CLI), *map(str, args)],
                                capture_output=True, text=True, timeout=10,
                                cwd=self.base)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def report(self, *args):
        return json.loads(self.run_cli(*args, '--json').stdout)

    def file(self, report, path):
        return next(item for item in report['files'] if item['path'] == str(path))

    def picked(self, report):
        return [item['path'] for item in report['files']
                if item['status'] in ('picked', 'truncated')]

    def test_nested_files_are_in_root_to_cwd_order(self):
        root = self.write('AGENTS.md', 'root')
        nested = self.write('src/deep/AGENTS.md', 'nested')
        r = self.report(nested.parent)
        self.assertEqual(r['schema_version'], 1)
        self.assertEqual(r['mode'], 'explain')
        self.assertEqual(r['root'], str(self.repo))
        self.assertEqual(self.picked(r), [str(root), str(nested)])
        self.assertEqual(r['used_bytes'], 10)

    def test_override_shadows_default(self):
        normal = self.write('AGENTS.md', 'normal')
        override = self.write('AGENTS.override.md', 'override')
        r = self.report(self.repo)
        self.assertEqual(self.file(r, normal)['status'], 'shadowed')
        self.assertEqual(self.file(r, override)['status'], 'picked')
        self.assertEqual(r['used_bytes'], 8)

    def test_empty_override_still_blocks_default(self):
        normal = self.write('AGENTS.md', 'normal')
        override = self.write('AGENTS.override.md', '')
        r = self.report(self.repo)
        self.assertEqual(self.file(r, override)['status'], 'empty')
        self.assertEqual(self.file(r, normal)['status'], 'shadowed')
        self.assertEqual(r['used_bytes'], 0)

    def test_whitespace_file_does_not_spend_budget(self):
        self.write('AGENTS.md', ' \n\t')
        child = self.write('child/AGENTS.md', 'abcd')
        r = self.report(child.parent, '--budget', 4)
        self.assertEqual(self.file(r, child)['status'], 'picked')
        self.assertEqual(r['used_bytes'], 4)

    def test_fallback_order_and_deduplication(self):
        first = self.write('TEAM.md', 'first')
        second = self.write('GUIDE.md', 'second')
        r = self.report(self.repo, '--fallback', 'TEAM.md', '--fallback', 'GUIDE.md',
                        '--fallback', 'TEAM.md')
        self.assertEqual(self.file(r, first)['status'], 'picked')
        self.assertEqual(self.file(r, second)['status'], 'shadowed')
        self.assertEqual(len([f for f in r['files'] if f['path'] == str(first)]), 1)

    def test_default_beats_fallback(self):
        normal = self.write('AGENTS.md', 'normal')
        fallback = self.write('TEAM.md', 'fallback')
        r = self.report(self.repo, '--fallback', 'TEAM.md')
        self.assertEqual(self.file(r, normal)['status'], 'picked')
        self.assertEqual(self.file(r, fallback)['status'], 'shadowed')

    def test_directory_named_agents_is_not_a_file(self):
        (self.repo / 'AGENTS.override.md').mkdir()
        normal = self.write('AGENTS.md', 'normal')
        r = self.report(self.repo)
        self.assertIn(str(normal), self.picked(r))

    def test_budget_truncates_and_blocks_deeper_files(self):
        root = self.write('AGENTS.md', 'abcdef')
        child = self.write('child/AGENTS.md', 'child')
        r = self.report(child.parent, '--budget', 3)
        self.assertEqual(self.file(r, root)['status'], 'truncated')
        self.assertEqual(self.file(r, root)['included_bytes'], 3)
        self.assertEqual(self.file(r, child)['status'], 'budget-exhausted')
        self.assertEqual(r['used_bytes'], 3)

    def test_zero_budget_loads_no_project_content(self):
        p = self.write('AGENTS.md', 'content')
        r = self.report(self.repo, '--budget', 0)
        self.assertEqual(self.file(r, p)['status'], 'budget-exhausted')
        self.assertEqual(r['used_bytes'], 0)

    def test_budget_counts_utf8_bytes_not_characters(self):
        p = self.write('AGENTS.md', '中文abc')
        r = self.report(self.repo, '--budget', 4)
        self.assertEqual(self.file(r, p)['size_bytes'], 9)
        self.assertEqual(self.file(r, p)['included_bytes'], 4)
        self.assertEqual(r['used_bytes'], 4)

    def test_global_is_opt_in_and_has_independent_budget(self):
        global_file = self.write('AGENTS.md', 'global contents', self.home)
        local_file = self.write('AGENTS.md', 'local')
        r = self.report(self.repo, '--codex-home', self.home, '--budget', 2)
        self.assertEqual(self.file(r, global_file)['scope'], 'global')
        self.assertEqual(self.file(r, global_file)['status'], 'picked')
        self.assertEqual(self.file(r, local_file)['status'], 'truncated')
        self.assertEqual(r['used_bytes'], 2)
        self.assertNotIn(str(global_file), self.picked(self.report(self.repo)))

    def test_empty_global_override_falls_back_to_normal(self):
        override = self.write('AGENTS.override.md', '  ', self.home)
        normal = self.write('AGENTS.md', 'global normal', self.home)
        r = self.report(self.repo, '--codex-home', self.home)
        self.assertEqual(self.file(r, override)['status'], 'empty')
        self.assertEqual(self.file(r, normal)['status'], 'picked')

    def test_global_override_precedes_normal(self):
        normal = self.write('AGENTS.md', 'normal', self.home)
        override = self.write('AGENTS.override.md', 'override', self.home)
        r = self.report(self.repo, '--codex-home', self.home)
        self.assertEqual(self.file(r, override)['status'], 'picked')
        self.assertEqual(self.file(r, normal)['status'], 'shadowed')

    def test_does_not_read_ancestors_above_root(self):
        outside = self.write('AGENTS.md', 'outside', self.base)
        local = self.write('AGENTS.md', 'local')
        r = self.report(self.repo)
        self.assertNotIn(str(outside), [f['path'] for f in r['files']])
        self.assertEqual(self.picked(r), [str(local)])

    def test_without_git_only_current_directory_is_considered(self):
        outer = self.base / 'plain'
        self.write('AGENTS.md', 'outer', outer)
        child = self.write('child/AGENTS.md', 'child', outer)
        # Some CI hosts place a .git marker above their temporary directory.
        # Isolate the no-marker contract from that ambient filesystem state.
        spec = importlib.util.spec_from_file_location('agent_scope_diff_under_test', CLI)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        original = Path.exists
        with patch.object(Path, 'exists', lambda p: False if p.name == '.git' else original(p)):
            r = module.explain(str(child.parent))
        self.assertEqual(r['root'], str(child.parent))
        self.assertEqual(self.picked(r), [str(child)])

    def test_git_marker_file_is_supported(self):
        plain = self.base / 'worktree'
        self.write('.git', 'gitdir: /fake/path', plain)
        p = self.write('AGENTS.md', 'root', plain)
        child = plain / 'src'
        child.mkdir()
        self.assertEqual(self.picked(self.report(child)), [str(p)])

    def test_explicit_root_overrides_discovery(self):
        root = self.write('AGENTS.md', 'outer')
        child = self.write('child/AGENTS.md', 'inner')
        r = self.report(child.parent, '--root', child.parent)
        self.assertNotIn(str(root), self.picked(r))
        self.assertEqual(self.picked(r), [str(child)])

    def test_no_instruction_bodies_in_json_or_human_output(self):
        secret = 'UNIQUE_SYNTHETIC_INSTRUCTION_TEXT_abc123'
        self.write('AGENTS.md', secret)
        self.assertNotIn(secret, self.run_cli(self.repo).stdout)
        self.assertNotIn(secret, self.run_cli(self.repo, '--json').stdout)

    def test_json_is_deterministic(self):
        self.write('AGENTS.md', 'test')
        self.assertEqual(self.run_cli(self.repo, '--json').stdout,
                         self.run_cli(self.repo, '--json').stdout)

    def test_compare_marks_directory_specific_additions_and_removals(self):
        self.write('AGENTS.md', 'shared')
        left = self.write('left/AGENTS.md', 'left')
        right = self.write('right/AGENTS.md', 'right')
        r = self.report('compare', left.parent, right.parent)
        self.assertEqual(r['mode'], 'compare')
        self.assertIn(str(left), [item['path'] for item in r['removed']])
        self.assertIn(str(right), [item['path'] for item in r['added']])

    def test_compare_same_path_has_no_changes(self):
        self.write('AGENTS.md', 'test')
        r = self.report('compare', self.repo, self.repo)
        self.assertEqual(r['added'], [])
        self.assertEqual(r['removed'], [])
        self.assertEqual(r['changed'], [])

    def test_nearest_git_marker_wins(self):
        self.write('AGENTS.md', 'outer')
        nested = self.repo / 'nested'
        nested.mkdir()
        (nested / '.git').mkdir()
        child = self.write('AGENTS.md', 'inner', nested)
        r = self.report(nested)
        self.assertEqual(r['root'], str(nested))
        self.assertEqual(self.picked(r), [str(child)])

    def test_symlink_instruction_is_read_like_regular_file(self):
        target = self.write('instructions.txt', 'synthetic')
        link = self.repo / 'AGENTS.md'
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest('Symlinks unavailable on this platform')
        r = self.report(self.repo)
        self.assertEqual(self.file(r, link)['status'], 'picked')
        self.assertEqual(r['used_bytes'], 9)

    def test_invalid_utf8_is_bounded_and_reported(self):
        path = self.repo / 'AGENTS.md'
        path.write_bytes(b'abc\xffdef')
        r = self.report(self.repo)
        self.assertEqual(r['used_bytes'], 7)
        self.assertTrue(r['warnings'])

    def test_help_and_version_are_available_without_repo(self):
        self.assertIn('--budget', self.run_cli('--help').stdout)
        self.assertIn('0.1.0', self.run_cli('--version').stdout)

    def test_invalid_paths_and_budget_fail_without_traceback(self):
        for args in [(self.base / 'missing',), (self.repo, '--budget', -1),
                     (self.repo, '--root', self.home),
                     (self.repo, '--fallback', '../outside.md')]:
            with self.subTest(args=args):
                result = self.run_cli(*args, ok=False)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
