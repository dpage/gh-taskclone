#!/usr/bin/env python3
#
# This tool will clone Github issues (titles, bodies, labels, and optionally
# milestones) from one repository to another.
#

import argparse
import os
import sys
import time
from pathlib import Path

import github3  # Ensure installed: pip install github3.py

# Global cache for target labels to avoid repeated API calls
target_labels_cache = None

def read_command_line():
    """Read the command line arguments."""
    parser = argparse.ArgumentParser(
        description='Copy tasks (issues) from one Github project to another.')
    parser.add_argument("--source-repo", required=True,
                        help="the source repo name")
    parser.add_argument("--source-owner", required=True,
                        help="the source repo owner name (org or user)")
    parser.add_argument("--target-repo", required=True,
                        help="the target repo name")
    parser.add_argument("--target-owner", required=True,
                        help="the target repo owner name (org or user)")
    parser.add_argument("--label", default="annual",
                        help="a label to limit copying to (default: annual). "
                             "Issues MUST have this label to be copied.")
    parser.add_argument("--clone-milestones", action="store_true",
                        help="Enable cloning of milestones and assignment "
                             "of issues to them. Default: False")
    parser.add_argument("--whitelist", default="",
                        help="a comma delimited list of labels to copy "
                             "(in addition to the selection label). "
                             "If omitted, all labels will be copied.")

    return parser.parse_args()


def get_issues(repo, label_name):
    """Fetches issues with a specific label, including their milestone."""
    print(f"\nFetching issues from '{repo.full_name}' with label '{label_name}'...")
    issues_data = []
    # repo.issues expects a list of labels
    for i in repo.issues(labels=[label_name], state='all'):
        # Skip pull requests if they appear
        if i.pull_request_urls:
            continue

        source_labels = list(i.labels()) # Get Label objects

        issue = {'title': i.title,
                 'body': i.body or '', # Ensure not None
                 'labels': source_labels, # List of Label objects
                 'milestone': i.milestone, # Milestone object or None
                 'number': i.number # Keep track of original number for info
                }
        issues_data.append(issue)
    print(f"Found {len(issues_data)} issues.")
    return issues_data


def create_labels(target_repo, source_labels, selection_label, whitelist_labels):
    """Ensures necessary labels exist in the target repo."""
    global target_labels_cache

    # Fetch and cache target labels once if not already done
    if target_labels_cache is None:
        print("Fetching existing labels from target repo...")
        target_labels_cache = {lbl.name: lbl for lbl in target_repo.labels()}
        print(f"Found {len(target_labels_cache)} labels in target.")

    for src_label in source_labels:
        # Check if we should copy this label
        if src_label.name == selection_label or \
           len(whitelist_labels) == 0 or \
           src_label.name in whitelist_labels:

            # Check if it exists in the target cache
            if src_label.name not in target_labels_cache:
                print(f'  Creating label: {src_label.name}')
                try:
                    new_label = target_repo.create_label(src_label.name, src_label.color)
                    target_labels_cache[new_label.name] = new_label # Add to cache
                except github3.exceptions.GitHubException as e:
                    # 422 often means it already exists (race condition/cache miss)
                    if e.code == 422:
                        print(f"    Label '{src_label.name}' likely already exists (422).")
                        # Try to fetch it to add to cache if possible
                        try:
                           target_labels_cache[src_label.name] = target_repo.label(src_label.name)
                        except github3.exceptions.GitHubException:
                           print(f"    Could not confirm existence of '{src_label.name}'.")
                    else:
                        print(f'    Error creating the label {src_label.name}: {e}')
                        sys.exit(1)


def clone_milestones(source_repo, target_repo):
    """Clones milestones from source_repo to dest_repo if they don't exist."""
    print("\nAttempting to clone milestones...")
    milestone_map = {} # Maps source_number -> target_object

    try:
        source_milestones_list = list(source_repo.milestones(state='all'))
        target_milestones_list = list(target_repo.milestones(state='all'))
        target_milestones_titles = {m.title: m for m in target_milestones_list}

        print(f"Found {len(source_milestones_list)} milestones in '{source_repo.full_name}'.")
        print(f"Found {len(target_milestones_list)} milestones in '{target_repo.full_name}'.")

        for sm in source_milestones_list:
            if sm.title in target_milestones_titles:
                dm = target_milestones_titles[sm.title]
                print(f"  - Milestone '{sm.title}' already exists.")
                milestone_map[sm.number] = dm
            else:
                print(f"  - Creating milestone '{sm.title}'...")
                try:
                    due_on_iso_string = sm.due_on.isoformat() if sm.due_on else None

                    dm = target_repo.create_milestone(
                        title=sm.title,
                        state=sm.state,
                        description=sm.description or '',
                        due_on=due_on_iso_string # <--- USE THE CONVERTED STRING
                    )
                    print(f"    -> Created milestone number {dm.number}.")
                    milestone_map[sm.number] = dm
                    target_milestones_titles[dm.title] = dm # Add to local map
                except github3.exceptions.GitHubException as e:
                    print(f"    !! Failed to create milestone '{sm.title}': {e}")

        print("Milestone cloning process finished.")
        return milestone_map

    except github3.exceptions.GitHubException as e:
        print(f"!! Error during milestone cloning: {e}")
        return milestone_map # Return whatever we managed to map


def create_issues(target_repo, issues_list, selection_label, whitelist_labels, milestone_map):
    """Creates issues in the target repository based on the source list."""
    print(f"\nStarting creation of {len(issues_list)} issues in '{target_repo.full_name}'...")
    created_count = 0
    batch_count = 0

    for issue in issues_list:
        print(f"\nProcessing source issue #{issue['number']}: '{issue['title']}'")

        # 1. Ensure labels exist
        create_labels(target_repo, issue['labels'], selection_label, whitelist_labels)

        # 2. Prepare labels list for new issue
        target_labels_names = []
        for l in issue['labels']:
            if l.name == selection_label or len(whitelist_labels) == 0 or l.name in whitelist_labels:
                target_labels_names.append(l.name)
        
        print(f"  - Using labels: {', '.join(target_labels_names) if target_labels_names else 'None'}")

        # 3. Prepare milestone number
        target_milestone_number = None
        if issue['milestone']:
            source_ms_number = issue['milestone'].number
            if source_ms_number in milestone_map:
                target_milestone_object = milestone_map[source_ms_number]
                target_milestone_number = target_milestone_object.number
                print(f"  - Assigning to milestone: '{target_milestone_object.title}' ({target_milestone_number})")
            else:
                print(f"  - Warning: Source milestone '{issue['milestone'].title}' (Number: {source_ms_number}) "
                      f"was not found or cloned to target. Skipping assignment.")

        # 4. Create the issue
        try:
            new_issue = target_repo.create_issue(
                title=issue['title'],
                body=issue['body'],
                labels=target_labels_names if target_labels_names else None,
                milestone=target_milestone_number
                # Assignees are not supported directly in create_issue with github3.py
            )
            print(f"  -> Successfully created issue #{new_issue.number}.")
            created_count += 1
            batch_count += 1

            # 5. Rate Limiting
            if batch_count >= 10:
                print('\nSleeping for 60 seconds to avoid Github\'s secondary rate limit...')
                time.sleep(60)
                batch_count = 0

        except github3.exceptions.GitHubException as e:
            print(f"  !! Failed to create issue '{issue['title']}': {e}")
            # Consider exiting or continuing. Exiting for now to match original script style.
            sys.exit(1)

    return created_count


if __name__ == '__main__':
    args = read_command_line()

    # Get the Github token
    GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN') # Simpler way to get env var
    if not GITHUB_TOKEN:
        try:
            token_path = Path.home() / '.github-token'
            with open(token_path) as f:
                GITHUB_TOKEN = f.readline().strip()
        except FileNotFoundError:
            pass

    if not GITHUB_TOKEN:
        print('No Github token could be found. Create ~/.github-token containing it, '
              'or set it in the GITHUB_TOKEN environment variable.')
        sys.exit(1)

    # Login to Github
    print("Logging into GitHub...")
    try:
        github_session = github3.login(token=GITHUB_TOKEN)
        # Check if login was successful (optional but good practice)
        if not github_session.me():
             print("Login failed. Check your token and permissions.")
             sys.exit(1)
        print("Login successful.")
    except Exception as e:
        print(f'Error logging into Github: {e}')
        sys.exit(1)

    # Get repository objects
    try:
        print(f"Accessing source repo: {args.source_owner}/{args.source_repo}")
        source_repo = github_session.repository(args.source_owner, args.source_repo)
        if not source_repo: # Check if repo object was returned
            print(f"Could not access source repo '{args.source_owner}/{args.source_repo}'. Check name/permissions.")
            sys.exit(1)
    except Exception as e:
        print(f'Error opening the source repository: {e}')
        sys.exit(1)

    try:
        print(f"Accessing target repo: {args.target_owner}/{args.target_repo}")
        target_repo = github_session.repository(args.target_owner, args.target_repo)
        if not target_repo: # Check if repo object was returned
            print(f"Could not access target repo '{args.target_owner}/{args.target_repo}'. Check name/permissions.")
            sys.exit(1)
    except Exception as e:
        print(f'Error opening the target repository: {e}')
        sys.exit(1)

    # Parse whitelist
    whitelist = [w.strip() for w in args.whitelist.split(',') if w.strip()]
    if whitelist:
        print(f"Using label whitelist: {whitelist}")

    # Perform the copy of milestones (if requested)
    milestone_map_data = {} # Initialize empty map
    if args.clone_milestones:
        print("\n-- Milestone Cloning Enabled --")
        milestone_map_data = clone_milestones(source_repo, target_repo)
    else:
        print("\n-- Milestone Cloning Disabled --")

    # Perform the copy of issues...
    source_issues_list = get_issues(source_repo, args.label)

    if not source_issues_list:
        print("\nNo issues matching the criteria found. Exiting.")
        sys.exit(0)

    num_created = create_issues(target_repo, source_issues_list, args.label, whitelist, milestone_map_data)

    print(f'\nFinished. Copied {num_created} tasks.')
