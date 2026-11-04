"""Unit tests for gh-taskclone.py. No network access, the Github API is mocked."""

import contextlib
import importlib.util
import io
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import github3

# The script name contains a hyphen, so it cannot be imported by name
SCRIPT = Path(__file__).resolve().parent.parent / 'gh-taskclone.py'
spec = importlib.util.spec_from_file_location('gh_taskclone', SCRIPT)
gtc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gtc)


def label(name, color='ff0000', description=''):
    return SimpleNamespace(name=name, color=color, description=description)


def api_error(code=None):
    e = github3.exceptions.GitHubException('API error')
    e.code = code
    return e


class TestCase(unittest.TestCase):
    """Silences output, skips sleeps and resets the label cache."""

    def setUp(self):
        gtc.target_labels_cache = None
        self.out = io.StringIO()
        stack = contextlib.ExitStack()
        stack.enter_context(contextlib.redirect_stdout(self.out))
        self.sleep = stack.enter_context(mock.patch.object(gtc.time, 'sleep'))
        self.addCleanup(stack.close)

    def assertExits(self, func, *args, code=1):
        with self.assertRaises(SystemExit) as cm:
            func(*args)
        self.assertEqual(cm.exception.code, code)


class TestHelpers(TestCase):

    def test_parse_label_list(self):
        self.assertEqual(gtc.parse_label_list(' a, b ,,c ,'), ['a', 'b', 'c'])
        self.assertEqual(gtc.parse_label_list(' , '), [])

    def test_label_key(self):
        self.assertEqual(gtc.label_key('Annual'), 'annual')

    def test_color_value(self):
        self.assertEqual(gtc.color_value('#ff0000'), 'ff0000')
        self.assertEqual(gtc.color_value('ff0000'), 'ff0000')
        self.assertEqual(gtc.color_value(None), '')


class TestReadCommandLine(TestCase):

    BASE = ['gh-taskclone.py', '--source-repo', 'src', '--source-owner', 'o',
            '--target-repo', 'tgt', '--target-owner', 'o']

    def parse(self, *extra):
        with mock.patch.object(sys, 'argv', self.BASE + list(extra)), \
                contextlib.redirect_stderr(io.StringIO()):
            return gtc.read_command_line()

    def test_defaults(self):
        args = self.parse()
        self.assertEqual(args.label, 'annual')
        self.assertFalse(args.clone_labels)
        self.assertFalse(args.clone_milestones)
        self.assertEqual(args.attach_labels_list, [])

    def test_attach_labels_list(self):
        self.assertEqual(self.parse('--attach-labels', 'bug, docs').attach_labels_list,
                         ['bug', 'docs'])

    def test_attach_labels_all_case_insensitive(self):
        self.assertEqual(self.parse('--attach-labels', 'ALL').attach_labels_list, ['all'])

    def test_attach_labels_empty_is_error(self):
        self.assertExits(self.parse, '--attach-labels', ' , ', code=2)

    def test_attach_labels_all_combined_is_error(self):
        self.assertExits(self.parse, '--attach-labels', 'all,bug', code=2)

    def test_missing_required_argument(self):
        with mock.patch.object(sys, 'argv', ['gh-taskclone.py']), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertExits(gtc.read_command_line, code=2)


class TestGetIssues(TestCase):

    def test_skips_pull_requests_and_defaults_body(self):
        ms = SimpleNamespace(number=3, title='2026')
        issue = mock.Mock(pull_request_urls=None, title='Book venue', body=None,
                          milestone=ms, number=7)
        issue.labels.return_value = iter([label('annual')])
        pr = mock.Mock(pull_request_urls={'url': 'x'})
        repo = mock.Mock(full_name='o/src')
        repo.issues.return_value = iter([issue, pr])

        result = gtc.get_issues(repo, 'annual')

        repo.issues.assert_called_once_with(labels=['annual'], state='all')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['title'], 'Book venue')
        self.assertEqual(result[0]['body'], '')
        self.assertEqual(result[0]['milestone'], ms)
        self.assertEqual(result[0]['number'], 7)
        self.assertEqual([l.name for l in result[0]['labels']], ['annual'])


class TestFetchLabels(TestCase):

    def test_returns_list(self):
        repo = mock.Mock()
        repo.labels.return_value = iter([label('a'), label('b')])
        self.assertEqual([l.name for l in gtc.fetch_labels(repo)], ['a', 'b'])

    def test_api_error_exits(self):
        repo = mock.Mock(full_name='o/src')
        repo.labels.side_effect = api_error()
        self.assertExits(gtc.fetch_labels, repo)


class TestTargetLabelsCache(TestCase):

    def test_built_from_given_labels(self):
        repo = mock.Mock()
        cache = gtc.get_target_labels_cache(repo, [label('Bug')])
        self.assertEqual(list(cache), ['bug'])
        repo.labels.assert_not_called()

    def test_fetched_once_from_api(self):
        repo = mock.Mock()
        repo.labels.return_value = iter([label('Bug')])
        first = gtc.get_target_labels_cache(repo)
        second = gtc.get_target_labels_cache(repo)
        self.assertIs(first, second)
        repo.labels.assert_called_once()


class TestSelectLabels(TestCase):

    LABELS = [label('Annual'), label('Bug'), label('docs'), label('annual')]

    def names(self, *args):
        return [l.name for l in gtc.select_labels(*args)]

    def test_selection_label_only(self):
        self.assertEqual(self.names(self.LABELS, 'annual', []), ['Annual'])

    def test_attach_labels_case_insensitive(self):
        self.assertEqual(self.names(self.LABELS, 'annual', ['BUG']), ['Annual', 'Bug'])

    def test_all(self):
        self.assertEqual(self.names(self.LABELS, 'annual', ['all']),
                         ['Annual', 'Bug', 'docs'])


class TestValidateAttachLabels(TestCase):

    def test_all_is_accepted(self):
        gtc.validate_attach_labels(['all'], 'annual', [], {})

    def test_labels_found_in_source_or_target(self):
        gtc.validate_attach_labels(['Bug', 'docs', 'annual'], 'annual',
                                   [label('bug')], {'docs': label('docs')})

    def test_missing_label_exits(self):
        self.assertExits(gtc.validate_attach_labels, ['nope', 'NOPE'], 'annual', [], {})
        self.assertIn("'nope'", self.out.getvalue())
        self.assertNotIn("'NOPE'", self.out.getvalue())

    def test_fragment_of_label_with_comma_exits(self):
        self.assertExits(gtc.validate_attach_labels, ['to do'], 'annual',
                         [label('to do, later')], {})
        self.assertIn('contains a comma', self.out.getvalue())


class TestCreateLabels(TestCase):

    def setUp(self):
        super().setUp()
        self.repo = mock.Mock()
        gtc.get_target_labels_cache(self.repo, [label('annual')])

    def test_creates_missing_label(self):
        self.repo.create_label.return_value = label('Bug')
        gtc.create_labels(self.repo, [label('annual'), label('Bug', '#00ff00', 'A bug')])
        self.repo.create_label.assert_called_once_with('Bug', '00ff00', 'A bug')
        self.assertIn('bug', gtc.target_labels_cache)

    def test_none_returned_exits(self):
        self.repo.create_label.return_value = None
        self.assertExits(gtc.create_labels, self.repo, [label('Bug')])

    def test_422_with_existing_label(self):
        self.repo.create_label.side_effect = api_error(422)
        self.repo.label.return_value = label('bug')
        gtc.create_labels(self.repo, [label('Bug')])
        self.assertIn('bug', gtc.target_labels_cache)

    def test_422_without_existing_label_exits(self):
        self.repo.create_label.side_effect = api_error(422)
        self.repo.label.side_effect = api_error(404)
        self.assertExits(gtc.create_labels, self.repo, [label('Bug')])

    def test_other_error_exits(self):
        self.repo.create_label.side_effect = api_error(500)
        self.assertExits(gtc.create_labels, self.repo, [label('Bug')])


class TestDiffLabels(TestCase):

    def test_create_update_and_unchanged(self):
        source = [label('new'),
                  label('Same', '#FF0000', 'd'),
                  label('recolour', '00ff00'),
                  label('redescribe', description='new text')]
        cache = {'same': label('same', 'ff0000', 'd'),
                 'recolour': label('recolour', 'ff0000'),
                 'redescribe': label('redescribe', description=None)}

        to_create, to_update = gtc.diff_labels(source, cache)

        self.assertEqual([l.name for l in to_create], ['new'])
        self.assertEqual([(s.name, c) for s, _, c in to_update],
                         [('recolour', ["color 'ff0000' -> '00ff00'"]),
                          ('redescribe', ['description'])])


class TestCloneLabels(TestCase):

    def setUp(self):
        super().setUp()
        self.source = mock.Mock(full_name='o/src')
        self.target = mock.Mock()
        self.existing = mock.Mock()
        self.existing.name = 'Bug'
        self.existing.color = 'ff0000'
        self.existing.description = ''
        gtc.get_target_labels_cache(self.target, [self.existing])

    def test_creates_and_updates(self):
        self.target.create_label.return_value = label('docs')
        self.existing.update.return_value = True

        gtc.clone_labels(self.source, self.target,
                         [label('docs', '#0000ff', 'Docs'), label('bug', '00ff00', 'Bug')])

        self.target.create_label.assert_called_once_with('docs', '0000ff', 'Docs')
        # The target keeps its own spelling of the name
        self.existing.update.assert_called_once_with('Bug', '00ff00', 'Bug')
        self.assertIn('1 created, 1 updated, 0 unchanged', self.out.getvalue())

    def test_create_error_exits(self):
        self.target.create_label.side_effect = api_error()
        self.assertExits(gtc.clone_labels, self.source, self.target, [label('docs')])

    def test_create_returning_none_exits(self):
        self.target.create_label.return_value = None
        self.assertExits(gtc.clone_labels, self.source, self.target, [label('docs')])

    def test_update_error_exits(self):
        self.existing.update.side_effect = api_error()
        self.assertExits(gtc.clone_labels, self.source, self.target,
                         [label('Bug', '00ff00')])

    def test_update_rejected_exits(self):
        self.existing.update.return_value = False
        self.assertExits(gtc.clone_labels, self.source, self.target,
                         [label('Bug', '00ff00')])


def milestone(number, title, due_on=None):
    return SimpleNamespace(number=number, title=title, state='open',
                           description=None, due_on=due_on)


class TestCloneMilestones(TestCase):

    def test_maps_existing_and_creates_missing(self):
        due = datetime(2026, 10, 1, tzinfo=timezone.utc)
        source = mock.Mock(full_name='o/src')
        source.milestones.return_value = iter([milestone(1, 'Old'), milestone(2, 'New', due)])
        target = mock.Mock(full_name='o/tgt')
        old = milestone(10, 'Old')
        target.milestones.return_value = iter([old])
        created = milestone(11, 'New')
        target.create_milestone.return_value = created

        result = gtc.clone_milestones(source, target)

        self.assertEqual(result, {1: old, 2: created})
        target.create_milestone.assert_called_once_with(
            title='New', state='open', description='', due_on=due.isoformat())

    def test_failed_create_is_skipped(self):
        source = mock.Mock(full_name='o/src')
        source.milestones.return_value = iter([milestone(1, 'New')])
        target = mock.Mock(full_name='o/tgt')
        target.milestones.return_value = iter([])
        target.create_milestone.side_effect = api_error()
        self.assertEqual(gtc.clone_milestones(source, target), {})

    def test_listing_error_returns_empty_map(self):
        source = mock.Mock(full_name='o/src')
        source.milestones.side_effect = api_error()
        self.assertEqual(gtc.clone_milestones(source, mock.Mock()), {})


class TestDeselectTasks(TestCase):

    def check(self, body, expected, count):
        self.assertEqual(gtc.deselect_tasks(body), (expected, count))

    def test_list_markers(self):
        self.check('- [x] a\n* [X] b\n+ [x] c\n1. [x] d\n2) [x] e\n- [ ] f',
                   '- [ ] a\n* [ ] b\n+ [ ] c\n1. [ ] d\n2) [ ] e\n- [ ] f', 5)

    def test_nested_and_blockquote(self):
        self.check('  - [x] a\n\t- [x] b\n> - [x] c\n>  1. [x] d',
                   '  - [ ] a\n\t- [ ] b\n> - [ ] c\n>  1. [ ] d', 4)

    def test_not_a_task(self):
        body = '- [x]foo\n[x] bar\n-[x] baz\ntext - [x] qux'
        self.check(body, body, 0)

    def test_crlf(self):
        self.check('- [x] a\r\n- [X] b\r\n', '- [ ] a\r\n- [ ] b\r\n', 2)

    def test_empty(self):
        self.check('', '', 0)

    def test_fenced_code_untouched(self):
        self.check('```\n- [x] in\n```\n~~~python\n- [x] in\n~~~\n- [x] out',
                   '```\n- [x] in\n```\n~~~python\n- [x] in\n~~~\n- [ ] out', 1)

    def test_fence_in_blockquote(self):
        self.check('> ```\n> - [x] in\n> ```\n- [x] out',
                   '> ```\n> - [x] in\n> ```\n- [ ] out', 1)

    def test_closing_fence_must_be_long_enough(self):
        self.check('````\n- [x] a\n```\n- [x] b\n````\n- [x] c',
                   '````\n- [x] a\n```\n- [x] b\n````\n- [ ] c', 1)

    def test_closing_fence_must_be_bare(self):
        self.check('```\n``` python\n- [x] in\n```\n- [x] out',
                   '```\n``` python\n- [x] in\n```\n- [ ] out', 1)

    def test_closing_fence_must_use_same_character(self):
        self.check('~~~\n```\n- [x] in\n~~~\n- [x] out',
                   '~~~\n```\n- [x] in\n~~~\n- [ ] out', 1)

    def test_unclosed_fence(self):
        self.check('```\n- [x] in', '```\n- [x] in', 0)

    def test_backtick_in_info_string_is_not_a_fence(self):
        self.check('``` a`b\n- [x] t', '``` a`b\n- [ ] t', 1)

    def test_tasklist_block(self):
        self.check('```[tasklist]\n- [x] t\n```\n- [x] u',
                   '```[tasklist]\n- [ ] t\n```\n- [ ] u', 2)


class TestCreateIssues(TestCase):

    def setUp(self):
        super().setUp()
        self.repo = mock.Mock(full_name='o/tgt')
        self.repo.create_issue.return_value = SimpleNamespace(number=100)
        gtc.get_target_labels_cache(self.repo, [label('Annual'), label('Bug')])

    def issue(self, n=1, milestone=None, body='- [x] task'):
        return {'title': f'Task {n}', 'body': body, 'labels': [label('annual')],
                'milestone': milestone, 'number': n}

    def test_creates_issue(self):
        ms = SimpleNamespace(number=1, title='2026')
        target_ms = SimpleNamespace(number=5, title='2026')

        count = gtc.create_issues(self.repo, [self.issue(milestone=ms)], 'annual', [],
                                  {1: target_ms})

        self.assertEqual(count, 1)
        self.repo.create_issue.assert_called_once_with(
            title='Task 1', body='- [ ] task', labels=['Annual'], milestone=5)

    def test_unmapped_milestone_is_skipped(self):
        ms = SimpleNamespace(number=1, title='2026')
        gtc.create_issues(self.repo, [self.issue(milestone=ms)], 'annual', [], {})
        self.assertIsNone(self.repo.create_issue.call_args.kwargs['milestone'])
        self.assertIn('Skipping assignment', self.out.getvalue())

    def test_sleeps_after_ten_issues(self):
        count = gtc.create_issues(self.repo, [self.issue(n) for n in range(11)],
                                  'annual', [], {})
        self.assertEqual(count, 11)
        self.sleep.assert_called_once_with(60)

    def test_api_error_exits(self):
        self.repo.create_issue.side_effect = api_error()
        self.assertExits(gtc.create_issues, self.repo, [self.issue()], 'annual', [], {})


if __name__ == '__main__':
    unittest.main()
