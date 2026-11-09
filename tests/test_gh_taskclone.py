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
import requests

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

    def fix_time(self, now=1000.0):
        patcher = mock.patch.object(gtc.time, 'time', return_value=now)
        patcher.start()
        self.addCleanup(patcher.stop)

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


class TestRateLimitResetWait(TestCase):

    def setUp(self):
        super().setUp()
        self.fix_time()

    def wait(self, headers):
        return gtc.rate_limit_reset_wait(SimpleNamespace(headers=headers))

    def test_not_used_up(self):
        self.assertIsNone(self.wait({}))
        self.assertIsNone(self.wait({'X-RateLimit-Remaining': '7',
                                     'X-RateLimit-Reset': '1100'}))

    def test_used_up_without_reset(self):
        self.assertIsNone(self.wait({'X-RateLimit-Remaining': '0'}))
        self.assertIsNone(self.wait({'X-RateLimit-Remaining': '0',
                                     'X-RateLimit-Reset': 'soon'}))

    def test_waits_until_reset_with_margin(self):
        self.assertEqual(self.wait({'X-RateLimit-Remaining': '0',
                                    'X-RateLimit-Reset': '1100'}), 105)

    def test_reset_in_the_past(self):
        self.assertEqual(self.wait({'X-RateLimit-Remaining': '0',
                                    'X-RateLimit-Reset': '900'}), 5)

    def test_wait_is_limited_to_an_hour(self):
        self.assertEqual(self.wait({'X-RateLimit-Remaining': '0',
                                    'X-RateLimit-Reset': '99999'}), 3600)


class FakeAdapter(requests.adapters.BaseAdapter):
    """Answers each request with the next (status, headers) pair."""

    def __init__(self, *answers):
        super().__init__()
        self.answers = list(answers)
        self.requests = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        status, headers = self.answers.pop(0)
        response = requests.Response()
        response.status_code = status
        response.headers.update(headers)
        response._content = b'{}'
        response.request = request
        response.url = request.url
        return response

    def close(self):
        pass


class TestWaitForRateLimit(TestCase):

    USED_UP = {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '1060'}

    def setUp(self):
        super().setUp()
        self.fix_time()

    def get(self, *answers):
        session = requests.Session()
        self.adapter = FakeAdapter(*answers)
        session.mount('https://', self.adapter)
        session.hooks['response'].append(gtc.wait_for_rate_limit(session))
        return session.get('https://api.github.com/repos/o/r')

    def test_success_is_untouched(self):
        self.assertEqual(self.get((200, {})).status_code, 200)
        self.sleep.assert_not_called()

    def test_other_forbidden_is_untouched(self):
        for headers in ({}, {'X-RateLimit-Remaining': '12', 'X-RateLimit-Reset': '1060'},
                        {'Retry-After': '30'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.get((403, headers)).status_code, 403)
                self.sleep.assert_not_called()

    def test_waits_for_reset_and_retries(self):
        for status in (403, 429):
            with self.subTest(status=status):
                self.sleep.reset_mock()
                response = self.get((status, self.USED_UP), (200, {}))
                self.assertEqual(response.status_code, 200)
                self.sleep.assert_called_once_with(65)
                self.assertEqual(len(self.adapter.requests), 2)
                self.assertEqual(self.adapter.requests[1].url,
                                 'https://api.github.com/repos/o/r')

    def test_retries_once_only(self):
        response = self.get((403, self.USED_UP), (403, self.USED_UP))
        self.assertEqual(response.status_code, 403)
        self.sleep.assert_called_once_with(65)
        self.assertEqual(len(self.adapter.requests), 2)

    def test_message(self):
        self.get((403, self.USED_UP), (200, {}))
        self.assertIn('rate limit is used up, waiting 65 seconds', self.out.getvalue())


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


class TestProjectArguments(TestCase):
    """Early checks of the project arguments (exit code 2, no network)."""

    def parse(self, *extra, source_owner='o', target_owner='o'):
        argv = ['gh-taskclone.py', '--source-repo', 'src', '--source-owner', source_owner,
                '--target-repo', 'tgt', '--target-owner', target_owner] + list(extra)
        with mock.patch.object(sys, 'argv', argv), \
                contextlib.redirect_stderr(io.StringIO()):
            return gtc.read_command_line()

    def test_defaults(self):
        args = self.parse()
        self.assertIsNone(args.target_project_number)
        self.assertIsNone(args.clone_project_number)
        self.assertFalse(args.allow_status_replace)

    def test_target_project_number_and_url(self):
        self.assertEqual(self.parse('--target-project', '3').target_project_number, 3)
        args = self.parse('--target-project', 'https://github.com/orgs/O/projects/4/')
        self.assertEqual(args.target_project_number, 4)

    def test_invalid_values(self):
        for option in ('--target-project', '--clone-project'):
            for value in ('abc', '0', '', 'https://github.com/orgs/o/projects/x'):
                with self.subTest(option=option, value=value):
                    extra = [option, value]
                    if option == '--clone-project':
                        extra += ['--target-project', '9']
                    self.assertExits(self.parse, *extra, code=2)

    def test_url_with_wrong_owner(self):
        self.assertExits(self.parse, '--target-project',
                         'https://github.com/orgs/other/projects/3', code=2)
        self.assertExits(self.parse, '--target-project', '9', '--clone-project',
                         'https://github.com/orgs/other/projects/3', code=2)

    def test_clone_project_uses_source_owner(self):
        args = self.parse('--target-project', '9', '--clone-project',
                          'https://github.com/orgs/Src/projects/3',
                          source_owner='src', target_owner='tgt')
        self.assertEqual(args.clone_project_number, 3)
        # The same URL is not valid for --target-project
        self.assertExits(lambda: self.parse('--target-project',
                                            'https://github.com/orgs/Src/projects/3',
                                            source_owner='src', target_owner='tgt'),
                         code=2)

    def test_clone_project_without_target_project(self):
        self.assertExits(self.parse, '--clone-project', '3', code=2)

    def test_same_project(self):
        self.assertExits(self.parse, '--target-project', '3', '--clone-project', '3', code=2)
        self.assertExits(self.parse, '--target-project', '3', '--clone-project', '003', code=2)
        self.assertExits(self.parse, '--target-project', '3', '--clone-project',
                         'https://github.com/users/O/projects/3/views/1', code=2)

    def test_same_number_with_different_owners_is_valid(self):
        args = self.parse('--target-project', '3', '--clone-project', '3',
                          source_owner='Src', target_owner='tgt')
        self.assertEqual((args.target_project_number, args.clone_project_number), (3, 3))

    def test_allow_status_replace_without_clone_project(self):
        self.assertExits(self.parse, '--allow-status-replace', code=2)
        self.assertExits(self.parse, '--target-project', '3', '--allow-status-replace', code=2)

    def test_valid_combinations(self):
        args = self.parse('--target-project', '3', '--clone-project', '2',
                          '--allow-status-replace')
        self.assertEqual((args.target_project_number, args.clone_project_number), (3, 2))
        self.assertTrue(args.allow_status_replace)
        self.assertEqual(self.parse('--target-project', '3').clone_project_number, None)


class TestParseProjectRef(TestCase):

    def test_number(self):
        self.assertEqual(gtc.parse_project_ref('3', 'o'), 3)
        self.assertEqual(gtc.parse_project_ref(' 12 ', 'o'), 12)

    def test_leading_zeros(self):
        self.assertEqual(gtc.parse_project_ref('007', 'o'), 7)

    def test_zero_rejected(self):
        for value in ('0', '000', 'https://github.com/orgs/o/projects/0'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                gtc.parse_project_ref(value, 'o')

    def test_not_a_number(self):
        for value in ('', 'abc', '-3', '3.5', '3a', '\u0663'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                gtc.parse_project_ref(value, 'o')

    def test_org_and_user_url(self):
        self.assertEqual(gtc.parse_project_ref('https://github.com/orgs/o/projects/3', 'o'), 3)
        self.assertEqual(gtc.parse_project_ref('https://github.com/users/o/projects/5', 'o'), 5)

    def test_case_insensitive_host_and_owner(self):
        self.assertEqual(gtc.parse_project_ref('https://GitHub.COM/orgs/MyOrg/projects/3', 'myorg'), 3)

    def test_optional_suffixes(self):
        for value in ('https://github.com/orgs/o/projects/3/',
                      'https://github.com/orgs/o/projects/3/views/2',
                      'https://github.com/orgs/o/projects/3/views/2/',
                      'https://github.com/orgs/o/projects/3?query=1',
                      'https://github.com/orgs/o/projects/3/views/2?layout=table&x=y'):
            with self.subTest(value=value):
                self.assertEqual(gtc.parse_project_ref(value, 'o'), 3)

    def test_owner_mismatch(self):
        with self.assertRaises(ValueError) as cm:
            gtc.parse_project_ref('https://github.com/orgs/other/projects/3', 'o')
        self.assertIn("'o'", str(cm.exception))

    def test_wrong_host_and_junk(self):
        for value in ('https://example.com/orgs/o/projects/3',
                      'https://github.com.evil.com/orgs/o/projects/3',
                      'https://github.com/orgs/o/projects/3abc',
                      'https://github.com/orgs/o/projects/3/foo',
                      'https://github.com/orgs/o/projects/3 junk',
                      'https://github.com/orgs/o/repos/3',
                      'https://github.com/orgs/o/projects/'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                gtc.parse_project_ref(value, 'o')


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


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def fake_response(status=200, data=None, errors=None, headers=None, text=''):
    body = {}
    if data is not None:
        body['data'] = data
    if errors is not None:
        body['errors'] = errors
    return SimpleNamespace(status_code=status, headers=headers or {}, text=text,
                           json=lambda: body)


def invalid_json_response():
    def fail():
        raise ValueError('no JSON')
    return SimpleNamespace(status_code=200, headers={}, text='<html>', json=fail)


def fake_session(*responses):
    session = mock.Mock()
    session.post.side_effect = list(responses)
    return session


def sent(session, n=0):
    """The 'input' variable of the n-th GraphQL call."""
    return session.post.call_args_list[n].kwargs['json']['variables']['input']


def option(name, color='GRAY', description=''):
    return {'id': 'opt-' + name, 'name': name, 'color': color, 'description': description}


def field(name, type_='DATE', options=None):
    return {'id': 'f-' + name, 'name': name, 'type': type_, 'options': options or []}


def status(*options, name='Status'):
    return field(name, 'SINGLE_SELECT', list(options))


def project(number=1, owner='o', fields=(), items=0, title='Board'):
    return {'owner': owner, 'number': number, 'id': f'P{number}', 'title': title,
            'url': f'https://github.com/orgs/{owner}/projects/{number}',
            'fields': list(fields), 'item_count': items}


def project_data(number=1, items=0, nodes=()):
    return {'repositoryOwner': {'projectV2': {
        'id': f'P{number}', 'title': 'Board', 'url': 'https://x/' + str(number),
        'items': {'totalCount': items}, 'fields': {'nodes': list(nodes)}}}}


class TestNameKey(TestCase):

    def test_name_key(self):
        self.assertEqual(gtc.name_key('Start DATE'), 'start date')


class TestGraphql(TestCase):

    def test_returns_data(self):
        session = fake_session(fake_response(data={'a': 1}))
        self.assertEqual(gtc.graphql(session, 'query', {'x': 1}), {'a': 1})
        args, kwargs = session.post.call_args
        self.assertEqual(args[0], 'https://api.github.com/graphql')
        self.assertEqual(kwargs['json'], {'query': 'query', 'variables': {'x': 1}})

    def test_graphql_errors_raise(self):
        session = fake_session(fake_response(
            data={'a': None}, errors=[{'type': 'NOT_FOUND', 'message': 'Could not resolve'}]))
        with self.assertRaises(gtc.GraphQLError) as cm:
            gtc.graphql(session, 'q')
        self.assertIn('Could not resolve', str(cm.exception))
        self.assertEqual(cm.exception.error_types, ['NOT_FOUND'])
        self.assertEqual(session.post.call_count, 1)

    def test_http_error_raises(self):
        session = fake_session(fake_response(status=500, text='Server broke'))
        with self.assertRaises(gtc.GraphQLError) as cm:
            gtc.graphql(session, 'q')
        self.assertIn('500', str(cm.exception))
        self.assertEqual(session.post.call_count, 1)

    def test_forbidden_without_rate_limit_is_not_retried(self):
        session = fake_session(fake_response(status=403, text='Resource not accessible'))
        with self.assertRaises(gtc.GraphQLError):
            gtc.graphql(session, 'q')
        self.assertEqual(session.post.call_count, 1)
        self.sleep.assert_not_called()

    def test_invalid_json_raises(self):
        with self.assertRaises(gtc.GraphQLError):
            gtc.graphql(fake_session(invalid_json_response()), 'q')

    def test_missing_data_raises(self):
        with self.assertRaises(gtc.GraphQLError):
            gtc.graphql(fake_session(fake_response()), 'q')

    def test_connection_error_raises(self):
        session = mock.Mock()
        session.post.side_effect = OSError('connection refused')
        with self.assertRaises(gtc.GraphQLError) as cm:
            gtc.graphql(session, 'q')
        self.assertIn('connection refused', str(cm.exception))

    def test_retry_after_header_is_used(self):
        for status in (403, 429):
            with self.subTest(status=status):
                self.sleep.reset_mock()
                session = fake_session(
                    fake_response(status=status, headers={'Retry-After': '7'}),
                    fake_response(data={'ok': True}))
                self.assertEqual(gtc.graphql(session, 'q'), {'ok': True})
                self.sleep.assert_called_once_with(7)
                self.assertEqual(session.post.call_count, 2)

    def test_rate_limit_message_without_header_waits_60(self):
        session = fake_session(
            fake_response(status=403, text='You have exceeded a secondary rate limit'),
            fake_response(data={'ok': True}))
        self.assertEqual(gtc.graphql(session, 'q'), {'ok': True})
        self.sleep.assert_called_once_with(60)

    def test_rate_limited_error_entry_retries(self):
        session = fake_session(
            fake_response(data={}, errors=[{'type': 'RATE_LIMITED', 'message': 'slow down'}]),
            fake_response(data={'ok': True}))
        self.assertEqual(gtc.graphql(session, 'q'), {'ok': True})
        self.sleep.assert_called_once_with(60)

    def test_attempts_are_limited(self):
        limited = fake_response(status=429, headers={'Retry-After': '5'})
        session = fake_session(limited, limited, limited, fake_response(data={}))
        with self.assertRaises(gtc.GraphQLError) as cm:
            gtc.graphql(session, 'q')
        self.assertEqual(session.post.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)
        self.assertIn('still active', str(cm.exception))
        self.assertIn('repeat the run later', str(cm.exception))

    def test_attempts_are_limited_for_rate_limited_error(self):
        limited = fake_response(data={}, errors=[{'type': 'RATE_LIMITED', 'message': 'x'}])
        session = fake_session(limited, limited, limited)
        with self.assertRaises(gtc.GraphQLError) as cm:
            gtc.graphql(session, 'q')
        self.assertEqual(session.post.call_count, 3)
        self.assertIn('still active', str(cm.exception))

    def test_used_up_rate_limit_waits_for_reset(self):
        self.fix_time()
        limited = fake_response(data={}, errors=[{'type': 'RATE_LIMITED', 'message': 'x'}],
                                headers={'X-RateLimit-Remaining': '0',
                                         'X-RateLimit-Reset': '1300'})
        session = fake_session(limited, fake_response(data={'ok': True}))
        self.assertEqual(gtc.graphql(session, 'q'), {'ok': True})
        self.sleep.assert_called_once_with(305)


class TestFetchProject(TestCase):

    def test_found(self):
        nodes = [{'id': 'f1', 'name': 'Status', 'dataType': 'SINGLE_SELECT',
                  'options': [option('Todo')]},
                 {'id': 'f2', 'name': 'Due date', 'dataType': 'DATE'},
                 {}, None]
        session = fake_session(fake_response(data=project_data(3, 5, nodes)))

        result = gtc.fetch_project(session, 'o', 3)

        self.assertEqual(session.post.call_args.kwargs['json']['variables'],
                         {'owner': 'o', 'number': 3})
        self.assertEqual((result['id'], result['title'], result['url'], result['item_count']),
                         ('P3', 'Board', 'https://x/3', 5))
        self.assertEqual([(f['name'], f['type']) for f in result['fields']],
                         [('Status', 'SINGLE_SELECT'), ('Due date', 'DATE')])
        self.assertEqual(result['fields'][0]['options'][0]['name'], 'Todo')
        self.assertEqual(result['fields'][1]['options'], [])

    def test_item_count_includes_archived_items(self):
        # Verified against the real API: items() without archivedStates skips
        # archived items (1 of 2 items), the guard needs all of them
        session = fake_session(fake_response(data=project_data(3, 2)))
        result = gtc.fetch_project(session, 'o', 3)
        query = session.post.call_args.kwargs['json']['query']
        self.assertRegex(query, r'items\(first: 1, archivedStates: \[ARCHIVED, NOT_ARCHIVED\]\)'
                                r' \{ totalCount \}')
        self.assertEqual(result['item_count'], 2)

    def check_not_found(self, response):
        self.assertExits(gtc.fetch_project, fake_session(response), 'myowner', 7)
        out = self.out.getvalue()
        self.assertIn("project 7 of 'myowner'", out)
        self.assertIn("'project' scope", out)
        self.assertIn('Projects read/write', out)
        self.assertIn('No changes were made.', out)

    def test_not_found_error(self):
        self.check_not_found(fake_response(
            data={'repositoryOwner': {'projectV2': None}},
            errors=[{'type': 'NOT_FOUND', 'message': 'Could not resolve to a ProjectV2'}]))

    def test_null_project(self):
        self.check_not_found(fake_response(data={'repositoryOwner': {'projectV2': None}}))

    def test_unknown_owner(self):
        self.check_not_found(fake_response(data={'repositoryOwner': None}))

    def test_other_error_exits_with_cause(self):
        self.assertExits(gtc.fetch_project, fake_session(fake_response(status=500, text='boom')), 'o', 1)
        self.assertIn('boom', self.out.getvalue())


class TestLoadProjects(TestCase):

    def args(self, target=None, clone=None, allow=False):
        return SimpleNamespace(target_owner='t', source_owner='s',
                               target_project_number=target,
                               clone_project_number=clone,
                               allow_status_replace=allow)

    def test_no_graphql_call_without_target_project(self):
        session = mock.Mock()
        self.assertEqual(gtc.load_projects(session, self.args()), (None, None))
        session.post.assert_not_called()

    def test_target_only(self):
        session = fake_session(fake_response(data=project_data(3)))
        target, plan = gtc.load_projects(session, self.args(target=3))
        self.assertEqual(target['number'], 3)
        self.assertIsNone(plan)
        self.assertEqual(session.post.call_count, 1)

    def test_target_and_source_with_plan(self):
        session = fake_session(fake_response(data=project_data(3)),
                               fake_response(data=project_data(2, nodes=[
                                   {'id': 'd', 'name': 'Due date', 'dataType': 'DATE'}])))
        target, plan = gtc.load_projects(session, self.args(target=3, clone=2))
        self.assertEqual(target['number'], 3)
        self.assertEqual(plan['date_fields'], ['Due date'])
        # Fetched with the owner of each project
        self.assertEqual(session.post.call_args_list[0].kwargs['json']['variables']['owner'], 't')
        self.assertEqual(session.post.call_args_list[1].kwargs['json']['variables']['owner'], 's')

    def test_missing_source_project_exits(self):
        session = fake_session(fake_response(data=project_data(3)),
                               fake_response(data={'repositoryOwner': None}))
        self.assertExits(gtc.load_projects, session, self.args(target=3, clone=2))

    def test_conflict_exits(self):
        session = fake_session(fake_response(data=project_data(3)),
                               fake_response(data=project_data(2, nodes=[
                                   {'id': 'd', 'name': 'Due date', 'dataType': 'TEXT'}])))
        self.assertExits(gtc.load_projects, session, self.args(target=3, clone=2))


class TestDiffProjectFields(TestCase):

    def diff(self, source, target):
        return gtc.diff_project_fields(project(2, fields=source), project(3, fields=target))

    def test_status_order_source_first_then_target_only(self):
        plan = self.diff([status(option('Todo', 'RED'), option('Done', 'GREEN'))],
                         [status(option('Extra'), option('Done', 'GREEN'), option('Other'))])
        st = plan['status']
        self.assertEqual([o['name'] for o in st['options']], ['Todo', 'Done', 'Extra', 'Other'])
        self.assertTrue(st['changes'])
        self.assertTrue(st['reordered'])
        self.assertEqual(st['field_id'], 'f-Status')

    def test_case_insensitive_match_keeps_target_spelling(self):
        plan = self.diff([status(option('in progress', 'BLUE', 'src text'))],
                         [status(option('In Progress', 'GRAY', 'old'))])
        st = plan['status']
        self.assertEqual(st['options'],
                         [{'name': 'In Progress', 'color': 'BLUE', 'description': 'src text'}])
        self.assertEqual((st['created'], st['updated'], st['reordered']), (0, 1, False))

    def test_counts(self):
        plan = self.diff([status(option('A', 'RED'), option('B', 'RED'), option('C', 'RED'))],
                         [status(option('A', 'RED'), option('B', 'BLUE'))])
        st = plan['status']
        self.assertEqual((st['created'], st['updated'], st['reordered']), (1, 1, False))

    def test_none_and_empty_description_are_equal(self):
        plan = self.diff([status(option('A', 'RED', None))], [status(option('A', 'RED', ''))])
        self.assertFalse(plan['status']['changes'])
        self.assertEqual(plan['status']['updated'], 0)
        plan = self.diff([status(option('A', 'RED', ''))], [status(option('A', 'RED', None))])
        self.assertFalse(plan['status']['changes'])

    def test_no_change_when_equal(self):
        opts = [option('A', 'RED', 'x'), option('B', 'BLUE', 'y')]
        plan = self.diff([status(*opts)], [status(*opts)])
        self.assertFalse(plan['status']['changes'])
        self.assertEqual(plan['date_fields'], [])
        self.assertEqual(plan['conflicts'], [])

    def test_name_spelling_difference_alone_is_no_change(self):
        plan = self.diff([status(option('todo', 'RED'))], [status(option('Todo', 'RED'))])
        self.assertFalse(plan['status']['changes'])

    def test_color_compared_exactly(self):
        plan = self.diff([status(option('A', 'RED'))], [status(option('A', 'red'))])
        self.assertTrue(plan['status']['changes'])

    def test_order_change_only(self):
        plan = self.diff([status(option('A'), option('B'))], [status(option('B'), option('A'))])
        st = plan['status']
        self.assertTrue(st['changes'])
        self.assertEqual((st['created'], st['updated'], st['reordered']), (0, 0, True))

    def test_target_status_missing_is_created(self):
        plan = self.diff([status(option('A'), option('B'))], [])
        st = plan['status']
        self.assertIsNone(st['field_id'])
        self.assertEqual(st['name'], 'Status')
        self.assertTrue(st['changes'])
        self.assertEqual(st['created'], 2)
        self.assertFalse(st['reordered'])

    def test_source_status_missing_is_skipped(self):
        plan = self.diff([], [status(option('A'))])
        self.assertIsNone(plan['status'])
        self.assertEqual(plan['skipped'], ['Status', 'Start date', 'Due date'])
        self.assertEqual(plan['conflicts'], [])

    def test_missing_source_date_field_is_no_conflict_whatever_the_target_has(self):
        plan = self.diff([], [field('Start date', 'TEXT'), field('Due date', 'NUMBER'),
                              status(option('A'), name='STATUS')])
        self.assertEqual(plan['conflicts'], [])
        self.assertEqual(plan['duplicates'], [])
        self.assertEqual(plan['date_fields'], [])

    def test_missing_source_status_whatever_the_target_has(self):
        plan = self.diff([], [field('Status', 'TEXT')])
        self.assertEqual(plan['conflicts'], [])

    def test_date_field_created_with_source_spelling(self):
        plan = self.diff([field('start DATE'), field('Due date')], [field('Due Date')])
        self.assertEqual(plan['date_fields'], ['start DATE'])

    def test_existing_date_field_left_alone(self):
        plan = self.diff([field('Start date')], [field('START DATE')])
        self.assertEqual(plan['date_fields'], [])
        self.assertEqual(plan['conflicts'], [])

    def test_target_date_field_not_date(self):
        plan = self.diff([field('Due date')], [field('Due date', 'TEXT')])
        self.assertEqual(len(plan['conflicts']), 1)
        self.assertIn("'Due date'", plan['conflicts'][0])
        self.assertIn('TEXT', plan['conflicts'][0])
        self.assertIn('target project 3', plan['conflicts'][0])

    def test_source_date_field_not_date(self):
        plan = self.diff([field('Start date', 'NUMBER')], [])
        self.assertEqual(len(plan['conflicts']), 1)
        self.assertIn('NUMBER', plan['conflicts'][0])
        self.assertIn('source project 2', plan['conflicts'][0])
        self.assertEqual(plan['date_fields'], [])

    def test_source_status_not_single_select(self):
        plan = self.diff([field('Status', 'TEXT')], [status(option('A'))])
        self.assertEqual(len(plan['conflicts']), 1)
        self.assertIn('TEXT', plan['conflicts'][0])
        self.assertIsNone(plan['status'])

    def test_target_status_not_single_select(self):
        plan = self.diff([status(option('A'))], [field('Status', 'TEXT')])
        self.assertEqual(len(plan['conflicts']), 1)
        self.assertIn('target project 3', plan['conflicts'][0])
        self.assertIsNone(plan['status'])

    def test_duplicate_target_field_names(self):
        plan = self.diff([field('Due date')], [field('Due date'), field('due DATE')])
        self.assertEqual(len(plan['conflicts']), 1)
        self.assertIn("'Due date'", plan['conflicts'][0])

    def test_duplicate_source_field_names(self):
        plan = self.diff([field('Due date'), field('due DATE')], [field('Due date')])
        self.assertEqual(len(plan['conflicts']), 1)
        self.assertIn("'Due date'", plan['conflicts'][0])
        self.assertIn('source project 2', plan['conflicts'][0])

    def test_duplicate_fields_not_copied_are_ignored(self):
        plan = self.diff([field('Due date')], [field('Due date'), field('Foo'), field('foo')])
        self.assertEqual(plan['conflicts'], [])

    def test_duplicate_option_names_in_source(self):
        plan = self.diff([status(option('A'), option('a'))], [status(option('A'))])
        self.assertEqual(len(plan['duplicates']), 1)
        self.assertIn("'a'", plan['duplicates'][0])
        self.assertIn('source project 2', plan['duplicates'][0])
        self.assertIsNone(plan['status'])

    def test_duplicate_option_names_in_target(self):
        plan = self.diff([status(option('A'))], [status(option('B'), option('b'))])
        self.assertEqual(len(plan['duplicates']), 1)
        self.assertIn('target project 3', plan['duplicates'][0])
        self.assertIsNone(plan['status'])

    def test_pure(self):
        self.diff([status(option('A'))], [])
        self.assertEqual(self.out.getvalue(), '')


class TestCheckProjectPlan(TestCase):
    """The --allow-status-replace guard."""

    def plan(self, source, target):
        return gtc.diff_project_fields(project(2, fields=source), project(3, fields=target))

    CHANGING = ([status(option('A', 'RED'))], [status(option('A', 'BLUE'))])

    def test_refuses_without_flag(self):
        plan = self.plan(*self.CHANGING)
        self.assertExits(gtc.check_project_plan, plan, project(3, items=4), False)
        out = self.out.getvalue()
        self.assertIn('4 items', out)
        self.assertIn('--allow-status-replace', out)
        self.assertIn('No changes were made.', out)

    def test_passes_with_flag_and_warns(self):
        plan = self.plan(*self.CHANGING)
        gtc.check_project_plan(plan, project(3, items=4), True)
        self.assertIn('Warning', self.out.getvalue())

    def test_no_refusal_when_options_unchanged(self):
        same = [status(option('A', 'RED'))]
        gtc.check_project_plan(self.plan(same, same), project(3, items=4), False)

    def test_no_refusal_when_target_status_is_created(self):
        gtc.check_project_plan(self.plan([status(option('A'))], []), project(3, items=4), False)

    def test_no_refusal_when_source_has_no_status(self):
        gtc.check_project_plan(self.plan([], [status(option('A'))]), project(3, items=4), False)

    def test_no_refusal_without_items(self):
        gtc.check_project_plan(self.plan(*self.CHANGING), project(3, items=0), False)
        self.assertNotIn('Warning', self.out.getvalue())

    def test_conflicts_exit_even_with_flag(self):
        plan = self.plan([field('Due date', 'TEXT')], [])
        self.assertExits(gtc.check_project_plan, plan, project(3), True)
        self.assertIn("'Due date'", self.out.getvalue())

    def test_duplicate_options_exit(self):
        plan = self.plan([status(option('A'), option('a'))], [])
        self.assertExits(gtc.check_project_plan, plan, project(3), True)


class TestCloneProject(TestCase):

    def plan(self, source, target):
        return gtc.diff_project_fields(project(2, fields=source), project(3, fields=target))

    def clone(self, plan, session=None):
        session = session or fake_session(*[fake_response(data={}) for _ in range(5)])
        gtc.clone_project(session, plan, project(3))
        return session

    def test_status_update_sends_complete_list_in_final_order(self):
        plan = self.plan([status(option('Todo', 'RED', 'd'), option('Done', 'GREEN'))],
                         [status(option('Extra', 'PINK', 'e'), option('done', 'GRAY'))])
        session = self.clone(plan)

        self.assertEqual(session.post.call_count, 1)
        self.assertEqual(sent(session), {
            'fieldId': 'f-Status',
            'singleSelectOptions': [
                {'name': 'Todo', 'color': 'RED', 'description': 'd'},
                {'name': 'done', 'color': 'GREEN', 'description': ''},
                {'name': 'Extra', 'color': 'PINK', 'description': 'e'}]})
        self.sleep.assert_called_once_with(1)
        self.assertIn('Status 3 options (1 created, 1 updated, reordered)', self.out.getvalue())

    def test_summary_variants(self):
        cases = [
            ([status(option('A'), option('B'))], [status(option('A'))], 'Status 2 options (1 created).'),
            ([status(option('A', 'RED'))], [status(option('A'))], 'Status 1 options (1 updated).'),
            ([status(option('A'), option('B'))], [status(option('B'), option('A'))],
             'Status 2 options (reordered).'),
            ([status(option('A'), option('B'))], [], 'Status 2 options (2 created).'),
        ]
        for source, target, expected in cases:
            with self.subTest(expected=expected):
                self.out.truncate(0)
                self.out.seek(0)
                self.clone(self.plan(source, target))
                self.assertIn('Project cloning finished: ' + expected, self.out.getvalue())

    def test_status_field_is_created(self):
        session = self.clone(self.plan([status(option('A', 'RED', 'x'))], []))
        self.assertEqual(sent(session), {
            'projectId': 'P3', 'dataType': 'SINGLE_SELECT', 'name': 'Status',
            'singleSelectOptions': [{'name': 'A', 'color': 'RED', 'description': 'x'}]})

    def test_date_fields_are_created(self):
        session = self.clone(self.plan([field('Start date'), field('Due date')],
                                       [field('due date')]))
        self.assertEqual(session.post.call_count, 1)
        self.assertEqual(sent(session), {'projectId': 'P3', 'dataType': 'DATE',
                                         'name': 'Start date'})
        self.assertIn('Project cloning finished: 1 field created.', self.out.getvalue())

    def test_status_and_two_date_fields(self):
        session = self.clone(self.plan(
            [status(option('A')), field('Start date'), field('Due date')], [status(option('B'))]))
        self.assertEqual(session.post.call_count, 3)
        self.assertEqual(self.sleep.call_count, 3)
        self.assertIn('Project cloning finished: Status 2 options (1 created), '
                      '2 fields created.', self.out.getvalue())

    def test_nothing_to_change(self):
        same = [status(option('A', 'RED')), field('Start date'), field('Due date')]
        session = mock.Mock()
        gtc.clone_project(session, self.plan(same, same), project(3))
        session.post.assert_not_called()
        self.sleep.assert_not_called()
        self.assertIn('Field \'Status\' is unchanged.', self.out.getvalue())
        self.assertIn('Project cloning finished: nothing to change.', self.out.getvalue())

    def test_skipped_fields_are_printed_from_the_plan(self):
        session = mock.Mock()
        gtc.clone_project(session, self.plan([], [field('Due date')]), project(3))
        out = self.out.getvalue()
        for name in ('Status', 'Start date', 'Due date'):
            self.assertIn(f"Field '{name}' does not exist in the source project, skipping.", out)
        self.assertIn('nothing to change', out)
        session.post.assert_not_called()

    def test_plan_is_not_recomputed(self):
        plan = self.plan([field('Due date')], [])
        with mock.patch.object(gtc, 'diff_project_fields') as diff:
            self.clone(plan)
        diff.assert_not_called()

    def test_failing_mutation_exits_with_field_name(self):
        plan = self.plan([status(option('A'))], [])
        session = fake_session(fake_response(data={}, errors=[{'message': 'nope'}]))
        self.assertExits(gtc.clone_project, session, plan, project(3))
        out = self.out.getvalue()
        self.assertIn("'Status'", out)
        self.assertIn('nope', out)
        self.assertIn('kept', out)

    def test_failing_date_field_exits_with_field_name(self):
        plan = self.plan([field('Due date')], [])
        session = fake_session(fake_response(status=500, text='boom'))
        self.assertExits(gtc.clone_project, session, plan, project(3))
        self.assertIn("'Due date'", self.out.getvalue())


class TestCreateIssuesWithProject(TestCase):

    def setUp(self):
        super().setUp()
        self.repo = mock.Mock(full_name='o/tgt')
        self.counter = 0
        self.repo.create_issue.side_effect = self.new_issue
        self.node_ids = {}
        gtc.get_target_labels_cache(self.repo, [label('Annual')])
        self.project = project(3, title='Board')
        self.session = mock.Mock()
        self.session.post.side_effect = lambda *a, **k: fake_response(data={})

    def new_issue(self, **kwargs):
        self.counter += 1
        number = 100 + self.counter
        node = self.node_ids.get(number, f'I_{number}')
        return SimpleNamespace(number=number, as_dict=lambda: {'node_id': node} if node else {})

    def issue(self, n):
        return {'title': f'Task {n}', 'body': '', 'labels': [label('annual')],
                'milestone': None, 'number': n}

    def create(self, numbers, project='default'):
        project = self.project if project == 'default' else project
        return gtc.create_issues(self.repo, [self.issue(n) for n in numbers], 'annual', [], {},
                                 project, self.session)

    def test_each_issue_is_attached(self):
        self.assertEqual(self.create([1, 2]), 2)
        self.assertEqual(self.session.post.call_count, 2)
        self.assertEqual(sent(self.session, 0), {'projectId': 'P3', 'contentId': 'I_101'})
        self.assertEqual(sent(self.session, 1), {'projectId': 'P3', 'contentId': 'I_102'})
        self.assertIn("-> Added to project 'Board' (project 3).", self.out.getvalue())

    def test_attach_sleeps_before_the_batch_sleep(self):
        self.create(range(10))
        self.assertEqual(self.sleep.call_args_list, [mock.call(1)] * 10 + [mock.call(60)])

    def test_missing_node_id_exits(self):
        self.node_ids[102] = None
        self.assertExits(self.create, [5, 6, 7])
        out = self.out.getvalue()
        self.assertNotIn('Failed to create issue', out)
        self.assertIn('#102', out)
        self.assertIn('source issue #6', out)
        self.assertIn('#5, #6', out)
        self.assertNotIn('#7', out)
        self.assertIn('no duplicate detection', out)
        self.assertIn('not rolled back', out)
        self.assertEqual(self.repo.create_issue.call_count, 2)

    def test_failed_add_exits(self):
        self.session.post.side_effect = [fake_response(data={}),
                                         fake_response(data={}, errors=[{'message': 'denied'}])]
        self.assertExits(self.create, [5, 6, 7])
        out = self.out.getvalue()
        self.assertNotIn('Failed to create issue', out)
        self.assertIn('#102', out)
        self.assertIn('source issue #6', out)
        self.assertIn('denied', out)
        self.assertIn('#5, #6', out)

    def test_issue_creation_failure_is_unchanged(self):
        self.repo.create_issue.side_effect = api_error()
        self.assertExits(self.create, [5])
        out = self.out.getvalue()
        self.assertIn('Failed to create issue', out)
        self.assertNotIn('already copied', out)
        self.session.post.assert_not_called()

    def test_without_project_no_graphql_call(self):
        self.assertEqual(self.create([1, 2], project=None), 2)
        self.session.post.assert_not_called()
        self.assertNotIn('project', self.out.getvalue().lower())


if __name__ == '__main__':
    unittest.main()
