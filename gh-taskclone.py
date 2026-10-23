#!/usr/bin/env python3
#
# This tool will clone Github issues (titles, bodies, and selected labels) from
# one repository to another. Optionally it also clones the repository labels
# (--clone-labels) and milestones (--clone-milestones).
#

import argparse
import os
import sys
import time
from pathlib import Path

import github3  # Ensure installed: pip install github3.py

# Global cache for target labels to avoid repeated API calls
target_labels_cache = None

def parse_label_list(value):
    """Split a comma delimited list of label names, dropping empty entries."""
    return [w.strip() for w in value.split(',') if w.strip()]


def read_command_line():
    """Read the command line arguments."""
    parser = argparse.ArgumentParser(
        description='Copy tasks (issues) from one Github project to another, '
                    'optionally including labels and milestones.')
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
    parser.add_argument("--clone-labels", action="store_true",
                        help="Enable cloning of all labels of the source repo "
                             "(name, color, description) to the target repo. "
                             "Missing labels are created, existing labels get "
                             "color and description overwritten from the "
                             "source. Labels are never deleted. Default: False")
    parser.add_argument("--attach-labels", default=None,
                        help="a comma delimited list of labels to attach to "
                             "the copied issues (in addition to the selection "
                             "label), or 'all' to attach all labels of the "
                             "source issue. If omitted, only the selection "
                             "label is attached.")

    args = parser.parse_args()

    # Checks which need no API data
    attach_value = args.attach_labels
    option_name = "--attach-labels"

    args.attach_labels_list = []
    if attach_value is not None:
        args.attach_labels_list = parse_label_list(attach_value)
        if not args.attach_labels_list:
            parser.error(f"{option_name} must contain at least one label name "
                         f"(or 'all')")
        if 'all' in args.attach_labels_list and len(args.attach_labels_list) > 1:
            parser.error(f"'all' in {option_name} cannot be combined with other labels")

    return args


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


def label_key(name):
    """Normalized label name: Github label names are case-insensitive."""
    return name.lower()


def color_value(color):
    """A label color as sent to the API: no leading '#'."""
    return (color or '').lstrip('#')


def fetch_labels(repo):
    """Fetches all labels of a repo."""
    try:
        return list(repo.labels())
    except github3.exceptions.GitHubException as e:
        print(f"Error fetching labels from '{repo.full_name}': {e}")
        sys.exit(1)


def get_target_labels_cache(target_repo, target_labels=None):
    """Returns the target label cache (lowercased name -> label object),
    filling it from target_labels or from the API on first use."""
    global target_labels_cache

    if target_labels_cache is None:
        if target_labels is None:
            print("Fetching existing labels from target repo...")
            target_labels = fetch_labels(target_repo)
        # Avoid requesting labels multiple times, this will run into the
        # API limit really quick.
        target_labels_cache = {label_key(lbl.name): lbl for lbl in target_labels}
        print(f"Found {len(target_labels_cache)} labels in target.")
    return target_labels_cache


def select_labels(issue_labels, selection_label, attach_labels):
    """Returns the label objects of an issue which are to be attached to the
    copy. The selection label is always attached, attach_labels is a list of
    label names or ['all']."""
    if attach_labels == ['all']:
        wanted = None
    else:
        wanted = {label_key(selection_label)}
        wanted.update(label_key(n) for n in attach_labels)

    selected = []
    seen = set()
    for lbl in issue_labels:
        key = label_key(lbl.name)
        if key in seen:
            continue
        if wanted is None or key in wanted:
            seen.add(key)
            selected.append(lbl)
    return selected


def validate_attach_labels(attach_labels, selection_label, source_labels,
                           target_cache, clone_labels_enabled):
    """Exits if a label listed in --attach-labels is not available."""
    if attach_labels == ['all']:
        return

    source_by_key = {label_key(lbl.name): lbl for lbl in source_labels}
    selection_key = label_key(selection_label)
    missing = []
    for name in attach_labels:
        key = label_key(name)
        if key == selection_key or key in [label_key(m) for m in missing]:
            continue
        if key in target_cache or (clone_labels_enabled and key in source_by_key):
            continue
        missing.append(name)

    if not missing:
        return

    # A comma in a label name cannot be expressed in the comma delimited list
    for name in missing:
        for lbl in list(source_labels) + list(target_cache.values()):
            fragments = [f.strip().lower() for f in lbl.name.split(',')]
            if ',' in lbl.name and label_key(name) in fragments:
                print(f"Error: '{name}' is part of the label '{lbl.name}', which "
                      f"contains a comma. Such labels cannot be listed in "
                      f"--attach-labels, use 'all' instead.")
                sys.exit(1)

    print("Error: these labels given in --attach-labels do not exist: " +
          ", ".join(f"'{n}'" for n in missing))
    in_source = [n for n in missing if label_key(n) in source_by_key]
    if in_source and not clone_labels_enabled:
        print("  --clone-labels would create these labels from the source repo: " +
              ", ".join(f"'{n}'" for n in in_source))
    print("No changes were made.")
    sys.exit(1)


def create_labels(target_repo, selected_labels):
    """Ensures the selected labels exist in the target repo."""
    cache = get_target_labels_cache(target_repo)

    for src_label in selected_labels:
        # Check if it exists in the target cache
        if label_key(src_label.name) not in cache:
            print(f"  Creating label '{src_label.name}'")
            try:
                new_label = target_repo.create_label(src_label.name,
                                                     color_value(src_label.color),
                                                     src_label.description or '')
                if new_label is None:
                    print(f"    Error creating the label '{src_label.name}': no label returned")
                    sys.exit(1)
                cache[label_key(new_label.name)] = new_label # Add to cache
            except github3.exceptions.GitHubException as e:
                # 422 often means it already exists (race condition/cache miss)
                if e.code == 422:
                    print(f"    Label '{src_label.name}' likely already exists (422).")
                    # Try to fetch it to add to cache if possible
                    try:
                        existing = target_repo.label(src_label.name)
                    except github3.exceptions.GitHubException:
                        existing = None
                    if existing is None:
                        print(f"    Could not confirm existence of '{src_label.name}'.")
                        sys.exit(1)
                    cache[label_key(existing.name)] = existing
                else:
                    print(f"    Error creating the label '{src_label.name}': {e}")
                    sys.exit(1)
            # Label writes: sleep to stay below Github's rate limits
            time.sleep(1)


def diff_labels(source_labels, target_cache):
    """Compares source labels with the target labels. Returns a list of
    labels to create and a list of (source, target, changes) to update.
    No API calls."""
    to_create = []
    to_update = []
    for src in source_labels:
        tgt = target_cache.get(label_key(src.name))
        if tgt is None:
            to_create.append(src)
            continue
        changes = []
        if color_value(src.color).lower() != color_value(tgt.color).lower():
            changes.append(f"color '{color_value(tgt.color)}' -> '{color_value(src.color)}'")
        if (src.description or '') != (tgt.description or ''):
            changes.append("description")
        if changes:
            to_update.append((src, tgt, changes))
    return to_create, to_update


def clone_labels(source_repo, target_repo, source_labels):
    """Clones labels from source_repo to target_repo: creates missing labels
    and syncs color and description of existing ones. Never deletes labels.
    Any failure is fatal."""
    print("\nAttempting to clone labels...")
    cache = get_target_labels_cache(target_repo)
    print(f"Found {len(source_labels)} labels in '{source_repo.full_name}'.")

    to_create, to_update = diff_labels(source_labels, cache)

    for src in to_create:
        print(f"  - Creating label '{src.name}'")
        try:
            new_label = target_repo.create_label(src.name, color_value(src.color),
                                                 src.description or '')
        except github3.exceptions.GitHubException as e:
            print(f"    !! Failed to create label '{src.name}': {e}")
            print("    Labels created or updated before this error were kept.")
            sys.exit(1)
        if new_label is None:
            print(f"    !! Failed to create label '{src.name}': no label returned")
            print("    Labels created or updated before this error were kept.")
            sys.exit(1)
        cache[label_key(new_label.name)] = new_label
        # Label writes: sleep to stay below Github's rate limits
        time.sleep(1)

    for src, tgt, changes in to_update:
        print(f"  - Updating label '{tgt.name}' ({', '.join(changes)})")
        try:
            # Keep the name of the target label, only color and description sync
            ok = tgt.update(tgt.name, color_value(src.color), src.description or '')
        except github3.exceptions.GitHubException as e:
            print(f"    !! Failed to update label '{tgt.name}': {e}")
            print("    Labels created or updated before this error were kept.")
            sys.exit(1)
        if not ok:
            print(f"    !! Failed to update label '{tgt.name}': the update was rejected")
            print("    Labels created or updated before this error were kept.")
            sys.exit(1)
        # Label writes: sleep to stay below Github's rate limits
        time.sleep(1)

    unchanged = len(source_labels) - len(to_create) - len(to_update)
    print(f"Label cloning finished: {len(to_create)} created, "
          f"{len(to_update)} updated, {unchanged} unchanged.")


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


def create_issues(target_repo, issues_list, selection_label, attach_labels, milestone_map):
    """Creates issues in the target repository based on the source list."""
    print(f"\nStarting creation of {len(issues_list)} issues in '{target_repo.full_name}'...")
    created_count = 0
    batch_count = 0

    for issue in issues_list:
        print(f"\nProcessing source issue #{issue['number']}: '{issue['title']}'")

        # Ensure labels exist
        selected_labels = select_labels(issue['labels'], selection_label, attach_labels)
        create_labels(target_repo, selected_labels)

        # Prepare labels list for new issue (spelling of the target labels)
        target_labels_names = [target_labels_cache[label_key(l.name)].name
                               for l in selected_labels]

        quoted_names = ", ".join("'" + n + "'" for n in target_labels_names)
        print(f"  - Using labels: {quoted_names if quoted_names else 'None'}")

        # Prepare milestone number
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

        # Create the actual issue
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

            # Avoid Rate Limiting
            if batch_count >= 10:
                print('\nSleeping for 60 seconds to avoid Github\'s secondary rate limit...')
                time.sleep(60)
                batch_count = 0

        except github3.exceptions.GitHubException as e:
            print(f"  !! Failed to create issue '{issue['title']}': {e}")
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

    # Fetch the labels once, validate before any write happens
    source_labels = fetch_labels(source_repo)
    target_cache = get_target_labels_cache(target_repo, fetch_labels(target_repo))
    validate_attach_labels(args.attach_labels_list, args.label, source_labels,
                           target_cache, args.clone_labels)

    # Perform the copy of labels (if requested)
    if args.clone_labels:
        print("\n-- Label Cloning Enabled --")
        clone_labels(source_repo, target_repo, source_labels)
    else:
        print("\n-- Label Cloning Disabled --")

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

    num_created = create_issues(target_repo, source_issues_list, args.label, args.attach_labels_list, milestone_map_data)

    print(f'\nFinished. Copied {num_created} tasks.')
