#!/usr/bin/env python

"""
Just a little script to report on the status of bugs slated against a given 
release.

Before running this, make sure that you have set your username in 
~/.bugzillarc:

[bugzilla.redhat.com]
user = someone@redhat.com

and that you have obtained a Bugzilla session cookie by executing:

$ bugzilla login
"""

BUGZILLA_URL = 'https://bugzilla.redhat.com/xmlrpc.cgi'
GERRIT_HOSTNAME = 'gerrit.beaker-project.org'
GERRIT_SSH_PORT = 29418

import sys
import os
import re
import subprocess
from itertools import chain
import simplejson as json
from optparse import OptionParser
import bugzilla # yum install python-bugzilla

# These are in Python 2.6
def any(iterable):
    for x in iterable:
        if x:
            return True
    return False
def all(iterable):
    for x in iterable:
        if not x:
            return False
    return True


################################################
# CLI helpers
################################################

def abbrev_user(user):
    if user.endswith('@redhat.com'):
        return user[:-len('@redhat.com')]

problems_found = False
def problem(message):
    global problems_found
    problems_found = True
    if os.isatty(sys.stdout.fileno()):
        print '\033[1m\033[91m** %s\033[0m' % message
    else:
        print '** %s' % message

def confirm(prompt):
    return raw_input(prompt + " (y/N)?:").lower().startswith('y')

################################################
# Version numbering helpers
################################################

# Historically Beaker's version numbers have not been so simple/regular, but 
# these days they are always 'x.y' where x and y are integers.

def increment_major(version):
    major, minor = [int(piece) for piece in version.split('.')]
    return '%s.%s' % (major + 1, 0)

def increment_minor(version):
    major, minor = [int(piece) for piece in version.split('.')]
    return '%s.%s' % (major, minor + 1)

def vercmp(left, right):
    return cmp(
            [int(piece) for piece in left.split('.')],
            [int(piece) for piece in right.split('.')])

################################################
# Bugzilla access
################################################

_status_order = [
    'NEW',
    'ASSIGNED',
    'POST',
    'MODIFIED',
    'ON_QA',
    'VERIFIED',
    'RELEASE_PENDING',
    'CLOSED'
]
_status_keys = dict((v, str(k)) for k, v in enumerate(_status_order))

def bug_sort_key(bug):
    status_key = _status_keys.get(bug.status, bug.status)
    return status_key, bug.assigned_to, bug.bug_id

class BugzillaInfo(object):

    def __init__(self, url=BUGZILLA_URL):
        self.url = url
        self._bz = None
        self._bz_cache = {}

    def get_bz_proxy(self):
        if self._bz is None:
            self._bz = bz = bugzilla.Bugzilla(url=self.url)
            # Make sure the user has logged themselves in properly, otherwise
            # we might accidentally omit private bugs from the list
            if not bz.user:
                raise RuntimeError('Configure your username in ~/.bugzillarc')
            if bz._proxy.User.valid_cookie(dict(login=bz.user))['cookie_isvalid'] != 1:
                raise RuntimeError('Invalid BZ credentials, try running "bugzilla login"')
        return self._bz

    def get_bugs(self, milestone=None, states=None, assignee=None):
        bz = self.get_bz_proxy()
        criteria = {'product': 'Beaker'}
        if milestone:
            criteria['target_milestone'] = milestone
        if states:
            criteria['status'] = list(states)
        if assignee:
            criteria['assigned_to'] = assignee
        bugs = bz.query(bz.build_query(**criteria))
        for bug in bugs:
            self._bz_cache[bug.bug_id] = bug
        return sorted(bugs, key=bug_sort_key)

    def get_bug(self, bug_id):
        try:
            return self._bz_cache[bug_id]
        except KeyError:
            bz = self.get_bz_proxy()
            criteria = {'bug_id': bug_id}
            result = bz.query(bz.build_query(**criteria))
            if not result:
                raise RuntimeError("No bug found with ID %r" % bug_id)
            bug = self._bz_cache[bug_id] = result[0]
            return bug

    def set_target_milestone(self, bug_id, target_milestone, nomail=False):
        bz = self.get_bz_proxy()
        updates = bz.build_update(target_milestone=target_milestone)
        if nomail:
            updates['nomail'] = 1
        bz.update_bugs([bug_id], updates)

    def set_resolution(self, bug_id, resolution, nomail=False):
        bz = self.get_bz_proxy()
        updates = bz.build_update(resolution=resolution)
        if nomail:
            updates['nomail'] = 1
        bz.update_bugs([bug_id], updates)

# Simple module level API for the default Bugzilla URL
bz_info = BugzillaInfo()
get_bugs = bz_info.get_bugs
get_bug = bz_info.get_bug

################################################
# Gerrit access
################################################

class GerritInfo(object):

    def __init__(self, host=GERRIT_HOSTNAME, port=GERRIT_SSH_PORT):
        self.host = GERRIT_HOSTNAME
        self.port = str(GERRIT_SSH_PORT)

    def get_gerrit_changes(self, bug_ids):
        p = subprocess.Popen(['ssh',
                '-o', 'StrictHostKeyChecking=no', # work around ssh bug on RHEL5
                '-p', self.port, self.host,
                'gerrit', 'query', '--format=json', '--current-patch-set',
                ' OR '.join('bug:%d' % bug_id for bug_id in bug_ids)],
                stdout=subprocess.PIPE)
        stdout, _ = p.communicate()
        assert p.returncode == 0, p.returncode
        retval = []
        for line in stdout.splitlines():
            obj = json.loads(line)
            if obj.get('type') == 'stats':
                continue
            retval.append(obj)
        return retval


# Simple module level API for the default Gerrit host
_gerrit_info = GerritInfo()
get_gerrit_changes = _gerrit_info.get_gerrit_changes

def changes_for_bug(changes, bug_id):
    for change in changes:
        change_bugs = [int(t['id']) for t in change['trackingIds'] if t['system'] == 'Bugzilla']
        if bug_id in change_bugs:
            yield change

################################################
# Local git query
################################################

# TODO: switch this to dulwich?
class GitInfo(object):

    def __init__(self):
        self._revlist = None

    def _git_call(self, *args):
        command = ['git']
        command.extend(args)
        p = subprocess.Popen(command, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
        stdout, stderr = p.communicate()
        if p.returncode != 0:
            raise RuntimeError("Git call failed: %s" % stderr)
        return stdout

    def build_git_revlist(self):
        if self._revlist is None:
            git_status = self._git_call('status')
            if "branch is behind" in git_status:
                raise RuntimeError("Git clone is not up to date")
            self._revlist = self._git_call('rev-list', 'HEAD').splitlines()
        return self._revlist

    def git_commit_reachable(self, sha):
        return sha in self._revlist

    _bug_footer_pattern = re.compile(r'Bug:.*?(\d+)', re.I)
    def bugs_referenced_in_commits(self):
        """
        Returns a list of bug IDs mentioned in all commits from master to HEAD.
        """
        messages = self._git_call('log', '--pretty=%B', 'origin/master..HEAD')
        bug_ids = []
        for line in messages.splitlines():
            m = self._bug_footer_pattern.search(line)
            if m:
                bug_ids.append(int(m.group(1)))
        return bug_ids

    def current_git_branch(self):
        remote_ref_name = self._git_call('name-rev', '--refs=refs/remotes/origin/*', '--name-only', 'HEAD').strip()
        # Output will be either 'remotes/origin/release-22' or 
        # 'origin/release-22' depending on git version...
        return remote_ref_name.split('/')[-1]

    def current_version(self):
        tag = self._git_call('describe', '--abbrev=0', 'HEAD').strip()
        assert tag.startswith('beaker-')
        return tag[len('beaker-'):]

# Simple module level API for a git repo in the current working dir
_git_info = GitInfo()
git_commit_reachable = _git_info.git_commit_reachable
build_git_revlist = _git_info.build_git_revlist
bugs_referenced_in_commits = _git_info.bugs_referenced_in_commits
current_git_branch = _git_info.current_git_branch
current_version = _git_info.current_version


################################################
# Checking bug consistency across tools
################################################

# Currently:
#  - Bugzilla (bugzilla.redhat.com)
#  - Gerrit (gerrit.beaker-project.org)
#  - Git (local clone of git.beaker-project.org/beaker)
#
# Milestone tracking
#  - filters based on the target milestone in Bugzilla

def get_default_milestone():
    # Figure out what milestone we are interested based on the version 
    # currently checked out.
    # If we are on a release branch, we are working on x.y+1 (for example, 
    # release-22 branch with version 22.3 means we are interested in 22.4).
    # For all other branches, including develop, we are working on x+1.0 (for 
    # example, develop branch with version 22.3 means we are interested in 
    # 23.0).
    if current_git_branch().startswith('release-'):
        return increment_minor(current_version())
    else:
        return increment_major(current_version())

# These are the names of long-lived feature branches which are abandoned and/or 
# rebased and/or cherry-picked. That is, these are *not expected* to be merged 
# into HEAD.
# This is important because if we find a Gerrit patch set which was destined 
# for one of these branches, we *won't* complain if the commit is not 
# reachable, because it's not expected to be.
abandoned_feature_branches = [
    'results-reporting-improvements',
    'results-reporting-improvements-take2',
]

def main():
    parser = OptionParser('usage: %prog [options]',
            description='Reports on the state of Beaker bugs for a given milestone')
    parser.add_option('-m', '--milestone', metavar='MILESTONE',
            help='Check bugs slated for MILESTONE [default: guess from current checkout]')
    parser.add_option('-i', '--include', metavar='STATE', action="append",
            help='Include bugs in the specified state '
                 '(may be given multiple times)')
    parser.add_option('-q', '--quiet', action="store_false",
            dest="verbose", default=True,
            help='Only display problem reports')
    options, args = parser.parse_args()
    if not options.milestone:
        options.milestone = get_default_milestone()
        print "Using milestone %s" % options.milestone

    if options.verbose:
        print "Building git revision list for HEAD"
    build_git_revlist()
    if options.verbose:
        print "Retrieving bug list from Bugzilla"
    bugs = get_bugs(milestone=options.milestone, states=options.include)
    bug_ids = set(bug.bug_id for bug in bugs)
    if options.verbose:
        print "  Retrieved %d bugs" % len(bugs)

    if options.verbose:
        print "Retrieving code review details from Gerrit"
    changes = get_gerrit_changes(bug_ids)
    if options.verbose:
        print "  Retrieved %d patch reviews" % len(changes)

    # Consistency check on all bugs in the specified milestone
    for bug in bugs:
        if options.verbose:
            print 'Bug %-13d %-17s %-10s <%s>' % (bug.bug_id, bug.bug_status,
                    abbrev_user(bug.assigned_to), bug.weburl)
        bug_changes = list(changes_for_bug(changes, bug.bug_id))

        # print out summary of changes
        for change in sorted(bug_changes, key=lambda c: int(c['number'])):
            patch_set = change['currentPatchSet']
            verified = max(chain([None], (int(a['value'])
                    for a in patch_set.get('approvals', []) if a['type'] == 'Verified'))) or 0
            reviewed = max(chain([None], (int(a['value'])
                    for a in patch_set.get('approvals', []) if a['type'] == 'Code-Review'))) or 0
            if options.verbose:
                print '    Change %-6s %-17s %-10s <%s>' % (change['number'],
                        '%s (%d/%d)' % (change['status'], verified, reviewed),
                        change['owner']['username'], change['url'])

        # check for patch state inconsistencies
        unabandoned_bug_changes = [change for change in bug_changes
                if change['status'] != 'ABANDONED']
        if not unabandoned_bug_changes:
            # No patches exist, or they're all abandoned.
            # We accept closed states here because the bug might have been 
            # fixed by something other than a Beaker patch (like a beah patch, etc).
            acceptable_bug_states = ['NEW', 'ASSIGNED', 'ON_QA', 'VERIFIED', 'CLOSED']
        elif any(change['status'] != 'MERGED' for change in unabandoned_bug_changes):
            # Some patches are undergoing review.
            acceptable_bug_states = ['ASSIGNED', 'POST']
        else:
            # Patches exist and they are all merged.
            acceptable_bug_states = ['MODIFIED', 'ON_QA', 'VERIFIED', 'CLOSED']
        if bug.bug_status not in acceptable_bug_states:
            problem('Bug %s should be %s, not %s'
                    % (bug.bug_id, ' or '.join(acceptable_bug_states), bug.bug_status))

        if bug.bug_status == 'CLOSED' and bug.resolution == 'DUPLICATE':
            # beaker_dupe_clear Bugzilla rule actually does this for us
            problem('Bug %s should have no milestone since it is marked DUPLICATE' % bug.bug_id)

        # Check merge consistency
        for change in bug_changes:
            if change['status'] == 'MERGED' and change['project'] == 'beaker' and \
                    change['branch'] not in abandoned_feature_branches:
                sha = change['currentPatchSet']['revision']
                if not git_commit_reachable(sha):
                    problem('Bug %s: Commit %s is not reachable from HEAD '
                            ' (is this clone up to date?)' % (bug.bug_id, sha))

        if options.verbose:
            print

    # Check for commits which reference a bug not in this milestone
    if not options.include:
        if options.verbose:
            print "Checking commit bug references for consistency"
        for referenced_bug_id in bugs_referenced_in_commits():
            if referenced_bug_id not in bug_ids:
                referenced_bug = get_bug(referenced_bug_id)
                # We have found a patch referencing a bug which is not in our 
                # milestone. It could be a merge/cherry-pick of a bug which is 
                # already fixed in some release, or on the maintenance branch: 
                # those are not a problem.
                # Only raise the alarm if the referenced bug's milestone is 
                # newer or not set.
                if (referenced_bug.target_milestone == '---' or
                        referenced_bug.target_milestone == 'future_maint' or
                        vercmp(referenced_bug.target_milestone, options.milestone) > 0):
                    problem('Bug %s is referenced by a commit on this branch '
                            'but target milestone is %s'
                            % (referenced_bug.bug_id, referenced_bug.target_milestone))

    # Check for bugs with a missing milestone setting
    if not options.include:
        if options.verbose:
            print "Checking milestone and bug status consistency"
        # In progress bugs should always have a milestone
        _in_work_states = [
            'MODIFIED',
            'ON_QA',
            'VERIFIED',
            'RELEASE_PENDING',
        ]
        in_work_bugs = get_bugs(milestone=['---', 'future_maint'], states=_in_work_states)
        for no_milestone in in_work_bugs:
            problem('Bug %s status is %s but target milestone is not set' %
                            (no_milestone.bug_id, no_milestone.bug_status))


if __name__ == '__main__':
    main()
    sys.exit(1 if problems_found else 0)
