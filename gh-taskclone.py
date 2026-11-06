#!/usr/bin/env python3
#
# This tool will clone Github issues (titles, bodies, and selected labels) from
# one repository to another, task list items in the bodies are copied unchecked.
# Optionally it also clones the repository labels (--clone-labels) and
# milestones (--clone-milestones), adds the copies to a Github Project
# (--target-project), and clones the Status columns and the "Start date" and
# "Due date" fields of another Project (--clone-project). Projects (v2) are only
# available through the GraphQL API.
#

import argparse
import os
import re
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
        description='Copy tasks (issues) from one Github repository to another, '
                    'optionally including labels, milestones and Github '
                    'Project settings.')
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
    parser.add_argument("--target-project", default=None,
                        help="a Github Project (v2) of --target-owner to add "
                             "the copied issues to: the project number, or "
                             "the full project URL (for example "
                             "https://github.com/orgs/OWNER/projects/3). "
                             "Default: no project")
    parser.add_argument("--clone-project", default=None,
                        help="a Github Project (v2) of --source-owner (number "
                             "or full URL) to clone the Status options and "
                             "the 'Start date' and 'Due date' fields from "
                             "into --target-project. Requires "
                             "--target-project. Default: False")
    parser.add_argument("--allow-status-replace", action="store_true",
                        help="Required when --clone-project changes the "
                             "Status options of a target project which "
                             "already contains items: the update may drop the "
                             "Status value of these items. Requires "
                             "--clone-project. Default: False")

    args = parser.parse_args()

    # Checks which need no API data
    args.attach_labels_list = []
    if args.attach_labels is not None:
        # 'all' is matched case-insensitively, like label names
        args.attach_labels_list = ['all' if w.lower() == 'all' else w
                                   for w in parse_label_list(args.attach_labels)]
        if not args.attach_labels_list:
            parser.error("--attach-labels must contain at least one label name "
                         "(or 'all')")
        if 'all' in args.attach_labels_list and len(args.attach_labels_list) > 1:
            parser.error("'all' in --attach-labels cannot be combined with other labels")

    # Project arguments, resolved to project numbers
    args.target_project_number = None
    args.clone_project_number = None
    try:
        if args.target_project is not None:
            args.target_project_number = parse_project_ref(args.target_project,
                                                           args.target_owner)
    except ValueError as e:
        parser.error(f"--target-project: {e}")
    try:
        if args.clone_project is not None:
            args.clone_project_number = parse_project_ref(args.clone_project,
                                                          args.source_owner)
    except ValueError as e:
        parser.error(f"--clone-project: {e}")
    if args.clone_project is not None and args.target_project is None:
        parser.error("--clone-project requires --target-project")
    if (args.clone_project is not None
            and args.source_owner.lower() == args.target_owner.lower()
            and args.clone_project_number == args.target_project_number):
        parser.error("--clone-project and --target-project are the same "
                     "project, source and target project must differ")
    if args.allow_status_replace and args.clone_project is None:
        parser.error("--allow-status-replace requires --clone-project")

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
                           target_cache):
    """Exits if a label listed in --attach-labels exists in neither the source
    nor the target repo. Labels missing in the target are created on demand."""
    if attach_labels == ['all']:
        return

    source_by_key = {label_key(lbl.name): lbl for lbl in source_labels}
    selection_key = label_key(selection_label)
    missing = []
    for name in attach_labels:
        key = label_key(name)
        if key == selection_key or key in [label_key(m) for m in missing]:
            continue
        if key in target_cache or key in source_by_key:
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

    print("Error: these labels given in --attach-labels exist in neither the "
          "source nor the target repository: " +
          ", ".join(f"'{n}'" for n in missing))
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


# ---------------------------------------------------------------------------
# Projects (v2). github3.py only knows the retired classic projects, so all
# project access goes through the GraphQL API.
# ---------------------------------------------------------------------------

GRAPHQL_URL = 'https://api.github.com/graphql'
GRAPHQL_ATTEMPTS = 3 # Tries per call, the retry is for the rate limit only
RATE_LIMIT_WAIT = 60 # Seconds to wait when Github does not say how long

PROJECT_PERMISSION_HINT = ("the token needs the 'project' scope (classic token) "
                           "or the Projects read/write permission "
                           "(fine-grained token)")

# One anchored expression for the project URL: owner and number, an optional
# /views/<m> suffix, an optional trailing slash and an optional query string.
PROJECT_URL_RE = re.compile(
    r'^https://github\.com/(?:orgs|users)/([A-Za-z0-9-]+)/projects/([0-9]+)'
    r'(?:/views/[0-9]+)?/?(?:\?[^\s#]*)?$', re.IGNORECASE)

# The fields cloned by --clone-project, with the type they must have
PROJECT_FIELDS = (('Status', 'SINGLE_SELECT'),
                  ('Start date', 'DATE'),
                  ('Due date', 'DATE'))

# The fields are listed with the interface fields, so that fields of every
# type (also iteration fields) can be read. A project has far fewer than 100
# fields, so there is no pagination.
PROJECT_QUERY = """
query($owner: String!, $number: Int!) {
  repositoryOwner(login: $owner) {
    ... on ProjectV2Owner {
      projectV2(number: $number) {
        id
        title
        url
        # Archived items are left out by default, but they keep their Status
        items(first: 1, archivedStates: [ARCHIVED, NOT_ARCHIVED]) { totalCount }
        fields(first: 100) {
          nodes {
            ... on ProjectV2FieldCommon { id name dataType }
            ... on ProjectV2SingleSelectField {
              options { id name color description }
            }
          }
        }
      }
    }
  }
}
"""

CREATE_FIELD_MUTATION = """
mutation($input: CreateProjectV2FieldInput!) {
  createProjectV2Field(input: $input) {
    projectV2Field { ... on ProjectV2FieldCommon { id name } }
  }
}
"""

UPDATE_FIELD_MUTATION = """
mutation($input: UpdateProjectV2FieldInput!) {
  updateProjectV2Field(input: $input) {
    projectV2Field { ... on ProjectV2FieldCommon { id name } }
  }
}
"""

ADD_ITEM_MUTATION = """
mutation($input: AddProjectV2ItemByIdInput!) {
  addProjectV2ItemById(input: $input) {
    item { id }
  }
}
"""


class GraphQLError(Exception):
    """A failed GraphQL call. error_types holds the 'type' of each error
    entry returned by Github (for example 'NOT_FOUND')."""

    def __init__(self, message, error_types=()):
        super().__init__(message)
        self.error_types = list(error_types)


def name_key(name):
    """Normalized project field or option name: matched case-insensitively."""
    return name.lower()


def parse_project_ref(value, expected_owner):
    """Returns the project number (int) for a project number or a project URL
    of expected_owner. Raises ValueError with a message otherwise. No API
    calls."""
    text = value.strip()
    if re.fullmatch(r'[0-9]+', text):
        number = int(text)
    else:
        match = PROJECT_URL_RE.match(text)
        if not match:
            raise ValueError(f"'{value}' is neither a project number nor a "
                             f"project URL like "
                             f"https://github.com/orgs/OWNER/projects/3")
        if match.group(1).lower() != expected_owner.lower():
            raise ValueError(f"the project URL '{value}' does not point to a "
                             f"project of '{expected_owner}'")
        number = int(match.group(2))
    if number < 1:
        raise ValueError(f"'{value}': the project number must be greater than 0")
    return number


def retry_wait(response):
    """Seconds to wait before retrying a rate limited request."""
    try:
        return max(int(response.headers.get('Retry-After')), 0)
    except (TypeError, ValueError):
        return RATE_LIMIT_WAIT


def graphql(session, query, variables=None):
    """Runs a GraphQL query or mutation with the (token carrying) requests
    session and returns the 'data' dict. Raises GraphQLError for HTTP errors,
    invalid JSON and GraphQL errors (reported with HTTP 200). The only retry
    is for Github's rate limits, at most GRAPHQL_ATTEMPTS attempts in total."""
    for attempt in range(1, GRAPHQL_ATTEMPTS + 1):
        try:
            response = session.post(GRAPHQL_URL, json={'query': query,
                                                       'variables': variables or {}})
        except OSError as e:
            raise GraphQLError(f"the request failed: {e}")

        status = response.status_code
        wait = None # Set when the request was rate limited
        if status in (403, 429) and ('Retry-After' in response.headers
                                     or 'rate limit' in response.text.lower()):
            wait = retry_wait(response)
        elif not 200 <= status < 300:
            raise GraphQLError(f"HTTP status {status}: {response.text[:200]}")
        else:
            try:
                payload = response.json()
            except ValueError:
                raise GraphQLError("the response is not valid JSON")
            if not isinstance(payload, dict):
                raise GraphQLError("the response is not valid GraphQL JSON")
            errors = [e for e in payload.get('errors') or [] if isinstance(e, dict)]
            if payload.get('errors') and not errors:
                errors = [{'message': str(payload['errors'])}]
            if any(e.get('type') == 'RATE_LIMITED' for e in errors):
                wait = retry_wait(response)
            elif errors:
                raise GraphQLError("; ".join(e.get('message', 'unknown error')
                                             for e in errors),
                                   [e.get('type') for e in errors])
            elif not isinstance(payload.get('data'), dict):
                raise GraphQLError("the response contains no data")
            else:
                return payload['data']

        if attempt == GRAPHQL_ATTEMPTS:
            raise GraphQLError(f"Github's rate limit was still active after "
                               f"{GRAPHQL_ATTEMPTS} attempts, repeat the run "
                               f"later", ['RATE_LIMITED'])
        # A rate limited request was normally not executed, so a retry is safe
        # for addProjectV2ItemById (idempotent). createProjectV2Field is not
        # idempotent: a retry after an ambiguous failure could end in a
        # duplicate name error, which is fatal like any other error.
        print(f"  Github rate limit reached, waiting {wait} seconds "
              f"(attempt {attempt} of {GRAPHQL_ATTEMPTS})...")
        time.sleep(wait)


def run_mutation(session, query, variables):
    """Runs a GraphQL mutation. Every mutation is a write: sleep to stay below
    Github's secondary rate limit."""
    data = graphql(session, query, variables)
    time.sleep(1)
    return data


def fetch_project(session, owner, number):
    """Fetches a project (id, title, url, fields, number of items) of an
    organization or a user. Exits if it cannot be accessed. No writes."""
    print(f"Accessing project {number} of '{owner}'...")
    node = None
    try:
        data = graphql(session, PROJECT_QUERY, {'owner': owner, 'number': number})
        node = (data.get('repositoryOwner') or {}).get('projectV2')
    except GraphQLError as e:
        # A missing project and a missing permission both are NOT_FOUND
        if not {'NOT_FOUND', 'FORBIDDEN', 'INSUFFICIENT_SCOPES'} & set(e.error_types):
            print(f"Error fetching project {number} of '{owner}': {e}")
            print("No changes were made.")
            sys.exit(1)

    if not node:
        print(f"Error: project {number} of '{owner}' was not found or is not "
              f"accessible. The project may not exist, or the token may lack "
              f"access to it: {PROJECT_PERMISSION_HINT}.")
        print("No changes were made.")
        sys.exit(1)

    fields = []
    for f in (node.get('fields') or {}).get('nodes') or []:
        if f and f.get('id'):
            fields.append({'id': f['id'], 'name': f['name'],
                           'type': f['dataType'], 'options': f.get('options') or []})
    project = {'owner': owner, 'number': number, 'id': node['id'],
               'title': node['title'], 'url': node['url'], 'fields': fields,
               'item_count': node['items']['totalCount']}
    print(f"Found project '{project['title']}' with {project['item_count']} items.")
    return project


def project_desc(role, project):
    """Describes a project in messages: the source project 2 of 'owner'."""
    return f"the {role} project {project['number']} of '{project['owner']}'"


def find_fields(project, name):
    """All fields of a project with this name (matched case-insensitively)."""
    return [f for f in project['fields'] if name_key(f['name']) == name_key(name)]


def duplicate_option_names(options):
    """Names of options which are equal after normalization with an earlier one."""
    seen = set()
    duplicates = []
    for o in options:
        if name_key(o['name']) in seen:
            duplicates.append(o['name'])
        seen.add(name_key(o['name']))
    return duplicates


def same_options(a, b):
    """True if two option lists are equal in order, names (normalized), colors
    and descriptions (None and '' are equal)."""
    if len(a) != len(b):
        return False
    return all(name_key(x['name']) == name_key(y['name'])
               and x['color'] == y['color']
               and (x['description'] or '') == (y['description'] or '')
               for x, y in zip(a, b))


def plan_status_options(source_field, target_field):
    """Plans the Status options: all source options in source order, then the
    options which exist only in the target in their existing order. For
    matching options the source color and description win, the target keeps its
    spelling of the name. target_field is None if the field is to be created."""
    target_options = target_field['options'] if target_field else []
    target_by_key = {name_key(o['name']): o for o in target_options}
    source_keys = {name_key(o['name']) for o in source_field['options']}

    final = []
    created = updated = 0
    for so in source_field['options']:
        to = target_by_key.get(name_key(so['name']))
        final.append({'name': to['name'] if to else so['name'],
                      'color': so['color'],
                      'description': so['description'] or ''})
        if to is None:
            created += 1
        elif (to['color'] != so['color']
              or (to['description'] or '') != (so['description'] or '')):
            updated += 1
    for to in target_options:
        if name_key(to['name']) not in source_keys:
            final.append({'name': to['name'], 'color': to['color'],
                          'description': to['description'] or ''})

    # Reordered: the options which already exist in the target change their
    # relative order (new options in between do not count)
    old_order = [name_key(o['name']) for o in target_options]
    new_order = [name_key(o['name']) for o in final if name_key(o['name']) in target_by_key]
    return {'name': target_field['name'] if target_field else source_field['name'],
            'field_id': target_field['id'] if target_field else None,
            'options': final,
            'changes': target_field is None or not same_options(final, target_options),
            'created': created,
            'updated': updated,
            'reordered': old_order != new_order}


def diff_project_fields(source_project, target_project):
    """Compares the fields cloned by --clone-project in both projects. Returns
    the plan: 'status' (None, or the final option list, whether it changes
    and the counts), 'date_fields' (names to create), 'skipped' (fields which
    do not exist in the source), 'conflicts' and 'duplicates' (fatal problems,
    as messages). No API calls, nothing is printed."""
    plan = {'status': None, 'date_fields': [], 'skipped': [],
            'conflicts': [], 'duplicates': []}
    source_desc = project_desc('source', source_project)
    target_desc = project_desc('target', target_project)

    for name, expected in PROJECT_FIELDS:
        sources = find_fields(source_project, name)
        if not sources:
            plan['skipped'].append(name)
            continue
        if len(sources) > 1:
            plan['conflicts'].append(f"Field '{name}' exists more than once in "
                                     f"{source_desc} (names are matched "
                                     f"case-insensitively).")
            continue
        src = sources[0]
        if src['type'] != expected:
            plan['conflicts'].append(f"Field '{name}' in {source_desc} has the "
                                     f"type {src['type']}, expected {expected}.")
            continue

        targets = find_fields(target_project, name)
        if len(targets) > 1:
            plan['conflicts'].append(f"Field '{name}' exists more than once in "
                                     f"{target_desc} (names are matched "
                                     f"case-insensitively).")
            continue
        tgt = targets[0] if targets else None
        if tgt is not None and tgt['type'] != expected:
            plan['conflicts'].append(f"Field '{name}' in {target_desc} has the "
                                     f"type {tgt['type']}, expected {expected}.")
            continue

        if name != 'Status':
            if tgt is None:
                plan['date_fields'].append(src['name'])
            continue

        duplicates = False
        for desc, field in ((source_desc, src), (target_desc, tgt)):
            for dup in duplicate_option_names(field['options'] if field else []):
                plan['duplicates'].append(f"Field '{name}' in {desc} has the "
                                          f"option '{dup}' more than once "
                                          f"(names are matched case-insensitively).")
                duplicates = True
        if not duplicates:
            plan['status'] = plan_status_options(src, tgt)

    return plan


def check_project_plan(plan, target_project, allow_status_replace):
    """Exits if the plan has conflicts or duplicate option names, or if it
    changes the Status options of a target project which contains items
    without --allow-status-replace. Run before any write happens."""
    problems = plan['conflicts'] + plan['duplicates']
    if problems:
        print("Error: the project cannot be cloned:")
        for problem in problems:
            print(f"  - {problem}")
        print("No changes were made.")
        sys.exit(1)

    status = plan['status']
    if (status is None or status['field_id'] is None or not status['changes']
            or target_project['item_count'] == 0):
        return
    # The API replaces the whole option list of a single select field, and the
    # option input has no id field. Every option, including the kept ones, is
    # therefore expected to get a new internal id, and the items already in the
    # project are expected to lose their Status value (unverified).
    if not allow_status_replace:
        print(f"Error: the Status options of {project_desc('target', target_project)} "
              f"would change, and it contains {target_project['item_count']} "
              f"items. The update may drop the Status value of existing items. "
              f"Use --allow-status-replace to do it anyway.")
        print("No changes were made.")
        sys.exit(1)
    print(f"Warning: the Status options of {project_desc('target', target_project)} "
          f"are replaced, the {target_project['item_count']} existing items may "
          f"lose their Status value (--allow-status-replace).")


def load_projects(session, args):
    """Fetches the target project and, for --clone-project, the source project,
    and validates the cloning plan. Makes no GraphQL call without
    --target-project. Returns (target_project, plan), each None if not used.
    No writes."""
    if args.target_project_number is None:
        return None, None

    target_project = fetch_project(session, args.target_owner,
                                   args.target_project_number)
    if args.clone_project_number is None:
        return target_project, None

    source_project = fetch_project(session, args.source_owner,
                                   args.clone_project_number)
    plan = diff_project_fields(source_project, target_project)
    check_project_plan(plan, target_project, args.allow_status_replace)
    return target_project, plan


def clone_project(session, plan, target_project):
    """Clones the fields of the plan (see diff_project_fields) into the target
    project. The plan is not recomputed. Any failure is fatal."""
    print("\nAttempting to clone project fields...")
    for name in plan['skipped']:
        print(f"Field '{name}' does not exist in the source project, skipping.")

    parts = []
    status = plan['status']
    if status is not None:
        count = len(status['options'])
        options = [{'name': o['name'], 'color': o['color'],
                    'description': o['description']} for o in status['options']]
        if status['field_id'] is None:
            print(f"  - Creating field '{status['name']}' ({count} options)")
            query = CREATE_FIELD_MUTATION
            variables = {'input': {'projectId': target_project['id'],
                                   'dataType': 'SINGLE_SELECT',
                                   'name': status['name'],
                                   'singleSelectOptions': options}}
        elif status['changes']:
            print(f"  - Replacing the options of field '{status['name']}' ({count} options)")
            query = UPDATE_FIELD_MUTATION
            # The complete final list in one call, in the final order
            variables = {'input': {'fieldId': status['field_id'],
                                   'singleSelectOptions': options}}
        else:
            query = None
            print(f"  - Field '{status['name']}' is unchanged.")

        if query is not None:
            try:
                run_mutation(session, query, variables)
            except GraphQLError as e:
                print(f"    !! Failed to clone field '{status['name']}': {e}")
                print("    Changes made before this error were kept.")
                sys.exit(1)
            details = []
            if status['created']:
                details.append(f"{status['created']} created")
            if status['updated']:
                details.append(f"{status['updated']} updated")
            if status['reordered']:
                details.append("reordered")
            parts.append(f"Status {count} options" +
                         (f" ({', '.join(details)})" if details else ""))

    for name in plan['date_fields']:
        print(f"  - Creating field '{name}'")
        try:
            run_mutation(session, CREATE_FIELD_MUTATION,
                         {'input': {'projectId': target_project['id'],
                                    'dataType': 'DATE', 'name': name}})
        except GraphQLError as e:
            print(f"    !! Failed to create field '{name}': {e}")
            print("    Changes made before this error were kept.")
            sys.exit(1)
    if plan['date_fields']:
        n = len(plan['date_fields'])
        parts.append(f"{n} field{'s' if n != 1 else ''} created")

    print(f"Project cloning finished: {', '.join(parts) if parts else 'nothing to change'}.")


def add_issue_to_project(session, project, new_issue):
    """Adds a created issue to the project. Raises GraphQLError."""
    node_id = new_issue.as_dict().get('node_id')
    if not node_id:
        raise GraphQLError("the created issue has no node_id")
    run_mutation(session, ADD_ITEM_MUTATION,
                 {'input': {'projectId': project['id'], 'contentId': node_id}})


# A checked task list item: indentation and blockquote markers, a list marker,
# then "[x]". Group 1 is everything up to and including the opening bracket,
# group 2 the closing bracket. Only [ \t] is used, a "\r" must never match.
CHECKED_TASK_RE = re.compile(r'^([ \t>]*(?:[-*+]|\d{1,9}[.)])[ \t]+\[)[xX](\])(?=[ \t])')


def deselect_tasks(body):
    """Unchecks all task list items in an issue body, except inside fenced
    code blocks. Returns the new body and the number of items changed.
    Indented code blocks and fences opened on a list marker line are not
    detected."""
    lines = body.split('\n')
    count = 0
    fence_char = None # Fence character while inside a fence, else None
    fence_len = 0 # Length of the opening fence run
    fence_is_tasklist = False

    for n, line in enumerate(lines):
        stripped = line.lstrip(' \t>')
        is_fence = stripped.startswith('```') or stripped.startswith('~~~')

        if fence_char is not None:
            # A closing fence uses the same character, a run at least as long
            # as the opening one, and nothing else on the line
            if is_fence and stripped[0] == fence_char:
                run = len(stripped) - len(stripped.lstrip(fence_char))
                if run >= fence_len and not stripped[run:].strip():
                    fence_char = None
                    continue
            if not fence_is_tasklist:
                continue
        elif is_fence:
            info = stripped.lstrip(stripped[0]).strip()
            # A backtick fence cannot have a backtick in its info string
            if stripped[0] == '`' and '`' in info:
                pass
            else:
                fence_char = stripped[0]
                fence_len = len(stripped) - len(stripped.lstrip(fence_char))
                fence_is_tasklist = (info == '[tasklist]')
                continue

        lines[n], changed = CHECKED_TASK_RE.subn(r'\1 \2', line)
        count += changed

    return '\n'.join(lines), count


def create_issues(target_repo, issues_list, selection_label, attach_labels, milestone_map,
                  project=None, session=None):
    """Creates issues in the target repository based on the source list. If a
    project (see fetch_project) is given, each new issue is added to it, using
    the GraphQL session."""
    print(f"\nStarting creation of {len(issues_list)} issues in '{target_repo.full_name}'...")
    created_count = 0
    batch_count = 0
    copied = [] # Source issue numbers copied so far

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

        # Task list items start unchecked in the copy
        new_body, deselected = deselect_tasks(issue['body'])
        if deselected > 0:
            print(f"  - Deselected {deselected} task(s)")

        # Create the actual issue
        try:
            new_issue = target_repo.create_issue(
                title=issue['title'],
                body=new_body,
                labels=target_labels_names if target_labels_names else None,
                milestone=target_milestone_number
                # Assignees are not supported directly in create_issue with github3.py
            )
        except github3.exceptions.GitHubException as e:
            print(f"  !! Failed to create issue '{issue['title']}': {e}")
            sys.exit(1)

        print(f"  -> Successfully created issue #{new_issue.number}.")
        created_count += 1
        batch_count += 1
        copied.append(issue['number'])

        # Not part of the issue creation: a failure here has its own message
        if project is not None:
            try:
                add_issue_to_project(session, project, new_issue)
            except GraphQLError as e:
                print(f"  !! Failed to add issue #{new_issue.number} (copy of source "
                      f"issue #{issue['number']}) to project {project['number']}: {e}")
                print("     The issue itself was created and is not rolled back.")
                print("     Source issues copied in this run (including this one): " +
                      ", ".join(f"#{n}" for n in copied))
                print("     The script has no duplicate detection, a rerun would create "
                      "these issues again. Exclude or remove them by hand first.")
                sys.exit(1)
            print(f"  -> Added to project '{project['title']}' (project {project['number']}).")

        # Avoid Rate Limiting
        if batch_count >= 10:
            print('\nSleeping for 60 seconds to avoid Github\'s secondary rate limit...')
            time.sleep(60)
            batch_count = 0

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
                           target_cache)

    # Fetch and validate the projects, compute the project plan (no writes yet)
    graphql_session = github_session.session
    target_project, project_plan = load_projects(graphql_session, args)

    # Perform the copy of labels (if requested)
    if args.clone_labels:
        print("\n-- Label Cloning Enabled --")
        clone_labels(source_repo, target_repo, source_labels)
    else:
        print("\n-- Label Cloning Disabled --")

    # Perform the copy of the project fields (if requested)
    if args.clone_project is not None:
        print("\n-- Project Cloning Enabled --")
        clone_project(graphql_session, project_plan, target_project)
    else:
        print("\n-- Project Cloning Disabled --")

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

    num_created = create_issues(target_repo, source_issues_list, args.label, args.attach_labels_list, milestone_map_data,
                              target_project, graphql_session)

    print(f'\nFinished. Copied {num_created} tasks.')
