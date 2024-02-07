#
# This tool will clone Github issue *titles* from one repository to another.
# This is intended to be used with projects used to manage annual events for
# which there is a specific sub-set of tasks that need to be repeated every
# year. It does *not* clone comments etc; just the titles.
#

import argparse
import os
import sys
import time
from pathlib import Path

import github3


def read_command_line():
    """Read the command line arguments.
    Returns:
        ArgumentParser: The parsed arguments object
    """
    parser = argparse.ArgumentParser(
        description='Copy tasks (issue titles) from one Github project to another.')
    parser.add_argument("--source-repo", required=True,
                        help="the source repo name")
    parser.add_argument("--source-owner", required=True,
                        help="the source repo owner name (owner may be an org or user)")
    parser.add_argument("--target-repo", required=True,
                        help="the target project name")
    parser.add_argument("--target-owner", required=True,
                        help="the target repo owner name (owner may be an org or user)")
    parser.add_argument("--label", default="annual",
                        help="a label to limit copying to (default: annual)")
    parser.add_argument("--whitelist", default="",
                        help="a comma delimited list of labels to copy (in addition to the selection label. "
                             "If omitted (or empty), all labels will be copied.")

    return parser.parse_args()


def get_issues(repo, label):
    issues = []
    for i in repo.issues(labels=label, state='all'):
        labels = []
        for l in i.labels():
            labels.append(l)

        issue = {'title': i.title,
                 'body': i.body,
                 'labels': labels}
        issues.append(issue)

    return issues


def create_labels(repo, labels, label, whitelist):
    for l in labels:
        # Only create the default label, or a whitelisted one, if there is a whitelist
        if l.name == label or len(whitelist) == 0 or l.name in whitelist:
            # Does the label exist in the target repo?
            found = False
            for a in repo.labels():
                if a.name == l.name:
                    found = True
                    break

            # Create the label if needed
            if not found:
                print(f'Creating label: {l.name}')
                try:
                    repo.create_label(l.name, l.color)
                except Exception as e:
                    print(f'Error creating the label {l.name}: {e}')
                    sys.exit(1)


def create_issues(repo, issues, label, whitelist):
    c = 0
    for i in issues:
        create_labels(repo, i['labels'], label, whitelist)

        print(f'Creating issue: {i["title"]}')

        # Get the list of labels
        labels = []
        for l in i['labels']:
            if l.name == label or len(whitelist) == 0 or l.name in whitelist:
                labels.append(l.name)

        try:
            repo.create_issue(i['title'], body=i['body'], labels=labels)
        except Exception as e:
            print(f'Error creating the issue: {e}')
            sys.exit(1)

        c = c + 1

        if c > 10:
            print('Sleeping to avoid Github\'s secondary rate limit. Sigh...')
            time.sleep(60)
            c = 0


if __name__ == '__main__':

    args = read_command_line()

    # Get the Github token
    GITHUB_TOKEN = ''
    if 'GITHUB_TOKEN' in os.environ:
        GITHUB_TOKEN = os.environ['GITHUB_TOKEN']
    else:
        try:
            with open(os.path.join(Path.home(), '.github-token')) as f:
                GITHUB_TOKEN = f.readline().strip()
        except FileNotFoundError:
            pass

    if GITHUB_TOKEN == '':
        print('No Github token could be found. Create ~/.github-token containing it, '
              'or set it in the GITHUB_TOKEN environment variable.')
        sys.exit(1)

    # Login to Github
    github = github3.login(token=GITHUB_TOKEN)

    try:
        source_repo = github.repository(args.source_owner, args.source_repo)
    except Exception as e:
        print(f'Error opening the source repository: {e}')
        sys.exit(1)

    try:
        target_repo = github.repository(args.source_owner, args.target_repo)
    except Exception as e:
        print(f'Error opening the target repository: {e}')
        sys.exit(1)

    whitelist = []
    if len(args.whitelist):
        whitelist = args.whitelist.split(',')

    # Perform the copy...
    issues = get_issues(source_repo, args.label)
    create_issues(target_repo, issues, args.label, whitelist)

    print(f'Copied {len(issues)} tasks.')
