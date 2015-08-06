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

def problem(message):
    if os.isatty(sys.stdout.fileno()):
        print '\033[1m\033[91m** %s\033[0m' % message
    else:
        print '** %s' % message

def confirm(prompt):
    return raw_input(prompt + " (y/N)?:").lower().startswith('y')

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

    def _get_release_flag(self, release):
        return 'Beaker-%s' % release

    def get_bugs(self, milestone=None, release=None, sprint=None, states=None,
            assignee=None):
        bz = self.get_bz_proxy()
        criteria = {'product': 'Beaker'}
        if milestone:
            criteria['target_milestone'] = milestone
        if sprint:
            criteria['devel_whiteboard'] = sprint
        if release:
            criteria['flag'] = [self._get_release_flag(release) + '+']
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

    def is_acked_for_release(self, bug_id, release):
        bug = self.get_bug(bug_id)
        flag = self._get_release_flag(release)
        return bug.get_flag_status(flag) == "+"

    def ack_for_release(self, bug_id, release):
        bz = self.get_bz_proxy()
        flag = self._get_release_flag(release)
        bz.update_flags([bug_id], [{"name": flag, "status": "+"}])

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

# Simple module level API for a git repo in the current working dir
_git_info = GitInfo()
git_commit_reachable = _git_info.git_commit_reachable
build_git_revlist = _git_info.build_git_revlist


################################################
# Checking bug consistency across tools
################################################

# Currently:
#  - Bugzilla (bugzilla.redhat.com)
#  - Gerrit (gerrit.beaker-project.org)
#  - Git (local clone of git.beaker-project.org/beaker)

# Release tracking
#  - filters based on the release flag in Bugzilla
#
# Milestone tracking
#  - filters based on the target milestone in Bugzilla


def main():
    parser = OptionParser('usage: %prog [options]',
            description='Reports on the state of Beaker bugs for a given milestone')
    parser.add_option('-m', '--milestone', metavar='MILESTONE',
            help='Check bugs slated for MILESTONE')
    parser.add_option('-r', '--release', metavar='RELEASE',
            help='Check bugs approved for RELEASE (using flags)')
    #parser.add_option('-s', '--sprint', metavar='SPRINT',
    #        help='Check bugs approved for SPRINT (using devel whiteboard)')
    parser.add_option('-i', '--include', metavar='STATE', action="append",
            help='Include bugs in the specified state '
                 '(may be given multiple times)')
    parser.add_option('-q', '--quiet', action="store_false",
            dest="verbose", default=True,
            help='Only display problem reports')
    options, args = parser.parse_args()
    options.sprint = None
    if not (options.milestone or options.release or options.sprint):
        parser.error('Specify a milestone, release or sprint')

    if options.verbose:
        print "Building git revision list for HEAD"
    build_git_revlist()
    if options.verbose:
        print "Retrieving bug list from Bugzilla"
    bugs = get_bugs(options.milestone, options.release, options.sprint,
                    options.include)
    bug_ids = set(bug.bug_id for bug in bugs)
    if options.verbose:
        print "  Retrieved %d bugs" % len(bugs)

    if options.verbose:
        print "Retrieving code review details from Gerrit"
    changes = get_gerrit_changes(bug_ids)
    if options.verbose:
        print "  Retrieved %d patch reviews" % len(changes)

    # Consistency check on all bugs in the specified sprint, release or
    # milestone
    for bug in bugs:
        if options.verbose:
            print 'Bug %-13d %-17s %-10s <%s>' % (bug.bug_id, bug.bug_status,
                    abbrev_user(bug.assigned_to), bug.weburl)
        bug_changes = list(changes_for_bug(changes, bug.bug_id))

        # print out summary of changes
        for change in sorted(bug_changes, key=lambda c: int(c['number'])):
            patch_set = change['currentPatchSet']
            verified = max(chain([None], (int(a['value'])
                    for a in patch_set.get('approvals', []) if a['type'] == 'VRIF'))) or 0
            reviewed = max(chain([None], (int(a['value'])
                    for a in patch_set.get('approvals', []) if a['type'] == 'CRVW'))) or 0
            if options.verbose:
                print '    Change %-6s %-17s %-10s <%s>' % (change['number'],
                        '%s (%d/%d)' % (change['status'], verified, reviewed),
                        change['owner']['username'], change['url'])

        # check for patch state inconsistencies
        if bug.bug_status in ('NEW', 'ASSIGNED') and \
                any(change['status'] != 'ABANDONED' for change in bug_changes):
            if all(change['status'] == 'MERGED' for change in bug_changes):
                problem('Bug %s should be MODIFIED, not %s' % (bug.bug_id, bug.bug_status))
            else:
                problem('Bug %s should be POST, not %s' % (bug.bug_id, bug.bug_status))
        elif bug.bug_status == 'POST' and \
                not any(change['status'] == 'NEW' for change in bug_changes):
            if bug_changes and all(change['status'] == 'MERGED' for change in bug_changes):
                problem('Bug %s should be MODIFIED, not %s' % (bug.bug_id, bug.bug_status))
            else:
                problem('Bug %s should be ASSIGNED, not %s' % (bug.bug_id, bug.bug_status))
        elif bug.bug_status in ('MODIFIED', 'ON_DEV', 'ON_QA', 'VERIFIED', 'RELEASE_PENDING', 'CLOSED'):
            if bug.bug_status == 'CLOSED' and bug.resolution == 'DUPLICATE':
                if bug.dupe_of not in bug_ids:
                    dupe = get_bug(bug.dupe_of)
                    if dupe.bug_status != 'CLOSED':
                        for target_kind in "release", "milestone", "sprint":
                            if getattr(options, target_kind, False):
                                break
                        problem('Bug %s marked as DUPLICATE of %s, which is not in this %s'
                                                % (bug.bug_id, bug.dupe_of, target_kind))
            elif bug.bug_status == 'MODIFIED' and not bug_changes:
                problem('Bug %s should be ASSIGNED, not %s' % (bug.bug_id, bug.bug_status))
            elif not all(change['status'] in ('ABANDONED', 'MERGED') for change in bug_changes):
                problem('Bug %s should be POST, not %s' % (bug.bug_id, bug.bug_status))

        # Check for release/milestone inconsistencies
        if options.release:
            if options.release == "1.0":
                # All currently completed work for 1.0 should target 0.x
                if (not bug.target_milestone.startswith("0.") and
                    bug.target_milestone != "HOTFIX" and
                    (bug.bug_status in ('VERIFIED', 'RELEASE_PENDING')
                     or (bug.bug_status == 'CLOSED' and
                         bug.resolution == 'CURRENTRELEASE'))):
                    problem('Bug %s target milestone should be set earlier than %s' %
                                    (bug.bug_id, options.release))
            # Other checks for bugs not merely allocated to the release
            if bug.target_milestone != options.release:
                if bug.target_milestone == "---":
                    problem('Bug %s target milestone should be set to %s or earlier' %
                                (bug.bug_id, options.release))
                elif (bug.target_milestone == "HOTFIX" and
                    bug.get_flag_status("hot_fix") == "+"):
                    pass
                elif (bug.target_milestone.split('.')[0] >=
                    options.release.split('.')[0]):
                    # If the milestone doesn't match the release flag, it should
                    # refer to an earlier major version
                    problem('Bug %s target milestone should be %s or earlier, not %s' %
                                    (bug.bug_id, options.release, bug.target_milestone))

        # Check merge consistency
        for change in bug_changes:
            if change['status'] == 'MERGED' and change['project'] == 'beaker':
                sha = change['currentPatchSet']['revision']
                if not git_commit_reachable(sha):
                    problem('Bug %s: Commit %s is not reachable from HEAD '
                            ' (is this clone up to date?)' % (bug.bug_id, sha))

        if options.verbose:
            print

    # Check for bugs already on the upstream "want list" awaiting approval
    if options.release:
        if options.verbose:
            print "Checking release and milestone consistency"
        # check for target milestone set without the appropriate release flag
        target_bugs = get_bugs(options.release, None, None, options.include)
        approved_bug_ids = set(b.bug_id for b in bugs)
        for unapproved in [b for b in target_bugs if b.bug_id not in approved_bug_ids]:
            problem('Bug %s target milestone is set, but bug is not approved' %
                            (unapproved.bug_id,))

    # Check for bugs with a missing milestone setting
    if not options.include:
        if options.verbose:
            print "Checking milestone and bug status consistency"
        # In progress bugs should always have a milestone
        _in_work_states = [
            'ASSIGNED',
            'POST',
            'MODIFIED',
            'ON_QA',
            'VERIFIED',
            'RELEASE_PENDING',
        ]
        in_work_bugs = get_bugs("---", None, None, _in_work_states)
        for no_milestone in in_work_bugs:
            problem('Bug %s status is %s but target milestone is not set' %
                            (no_milestone.bug_id, no_milestone.bug_status))


if __name__ == '__main__':
    main()
