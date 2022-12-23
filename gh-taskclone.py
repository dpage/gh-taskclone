#
# This tool will clone Github issue *titles* from one repository to another.
# This is intended to be used with projects used to manage annual events for
# which there is a specific sub-set of tasks that need to be repeated every
# year. It does *not* clone comments etc; just the titles.
#

import argparse
import os
import sys
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

    return parser.parse_args()


def get_issues(repo, label):
    issues = []
    for issue in repo.issues(labels=label):
        issues.append(issue.title)

    return issues


def create_issues(repo, issues, label):
    for issue in issues:
        print(f'Creating: {issue}')
        try:
            repo.create_issue(issue, labels=[label])
        except Exception as e:
            print(f'Error creating the issue: {e}')
            sys.exit(1)


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

    # Does the label exist in the target repo?
    label_found = False
    for l in target_repo.labels():
        if l.name == args.label:
            label_found = True
            break

    # Create the label if needed
    if not label_found:
        try:
            target_repo.create_label(args.label, '#cfd3d7')
        except Exception as e:
            print(f'Error creating the label {args.label}: {e}')
            sys.exit(1)

    # Perform the copy...
    issues = get_issues(source_repo, args.label)
    create_issues(target_repo, issues, args.label)

    print(f'Copied {len(issues)} tasks.')
