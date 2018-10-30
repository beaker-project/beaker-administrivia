#!/usr/bin/python3

import sys
import os
from glob import glob
import math
from collections import namedtuple, Counter
import datetime
import json
import re
import lxml.etree

def dogfood_job_dirs():
    el6dir = '/srv/www/jenkins-results/beaker-review-checks-dogfood-RedHatEnterpriseLinux6'
    for jobnum in os.listdir(el6dir):
        yield os.path.join(el6dir, jobnum)
    el7dir = '/srv/www/jenkins-results/beaker-review-checks-dogfood-RedHatEnterpriseLinux7'
    for jobnum in os.listdir(el7dir):
        if int(jobnum) < 49:
            continue # builds before #49 were busted
        yield os.path.join(el7dir, jobnum)

class KnownIssue(object):

    def __init__(self, description, bug_id=None, failure_patterns=None, console_patterns=None):
        self.description = description
        self.bug_id = bug_id
        self.failure_patterns = [re.compile(patt, re.DOTALL)
                for patt in (failure_patterns or [])]
        self.console_patterns = [re.compile(patt, re.DOTALL)
                for patt in (console_patterns or [])]

    def matches_nose_output(self, output):
        if not self.failure_patterns:
            return False
        failures = re.split(rb'={70}\n|-{70}\nRan ', output)[1:-1]
        for failure in failures:
            for failure_pattern in self.failure_patterns:
                if failure_pattern.search(failure):
                    return True
        return False

    def matches_console_output(self, output):
        if not self.console_patterns:
            return False
        for console_pattern in self.console_patterns:
            if console_pattern.search(output):
                return True
        return False

known_issues = [
    KnownIssue(
        description='WebDriverException: Message: Can\'t load the profile',
        failure_patterns=[rb'WebDriverException: Message: Can\'t load the profile\.'],
    ),
    KnownIssue(
        description='WebDriver Connection refused',
        failure_patterns=[rb'webdriver\.Firefox\(.*create_connection.*Connection refused'],
    ),
    KnownIssue(
        description='/boot corrupted',
        console_patterns=[
            # error: not a correct XFS inode.
            rb'e\s*r\s*r\s*o\s*r\s*:\s+n\s*o\s*t\s+a\s+c\s*o\s*r\s*r\s*e\s*c\s*t\s+X\s*F\s*S\s+i\s*n\s*o\s*d\s*e\s*\.',
            # error: attempt to read or write outside of partition.
            rb'e\s*r\s*r\s*o\s*r\s*:\s+a\s*t\s*t\s*e\s*m\s*p\s*t\s+t\s*o\s+r\s*e\s*a\s*d\s+o\s*r\s+w\s*r\s*i\s*t\s*e\s+o\s*u\s*t\s*s\s*i\s*d\s*e\s+o\s*f\s+p\s*a\s*r\s*t\s*i\s*t\s*i\s*o\s*n\s*\.',
            # alloc magic is broken at 0x
            rb'a\s*l\s*l\s*o\s*c\s+m\s*a\s*g\s*i\s*c\s+i\s*s\s+b\s*r\s*o\s*k\s*e\s*n\s+a\s*t\s+0\s*x',
            # error: file `/grub2/i386-pc/bufio.mod' not found.
            rb'e\s*r\s*r\s*o\s*r\s*:\s+f\s*i\s*l\s*e\s+`.*\.\s*m\s*o\s*d\s*\'\s+n\s*o\s*t\s+f\s*o\s*u\s*n\s*d\s*\.',
        ],
    ),
    KnownIssue(
        # originally considered to be a dupe of bug 1336272 above,
        # but still occurring even though that one has been fixed
        description='dogfood tests can fail in MACAddressAllocationTest due to StaleTaskStatusException',
        bug_id='1346123',
        failure_patterns=[rb'MACAddressAllocationTest.*StaleTaskStatusException'],
    ),
    KnownIssue(
        # should be fixed by https://gerrit.beaker-project.org/#/c/beaker/+/6189
        description='race condition with recipe page reservation tab re-rendering',
        failure_patterns=[rb'Return the reservation.*StaleElementReferenceException'],
    ),
    KnownIssue(
        # maybe fixed by https://gerrit.beaker-project.org/#/c/beaker/+/6189 ?
        description='NoSuchElementException when returning a reserved recipe',
        failure_patterns=[rb'find_element_by_xpath.*button.*Returning\\u2026.*NoSuchElementException'],
    ),
    KnownIssue(
        # maybe fixed by https://gerrit.beaker-project.org/#/c/beaker/+/6189 ?
        description='watchdog not reduced to 0 when returning a reserved recipe',
        failure_patterns=[rb'status_watchdog.*AssertionError: .* not less than or equal to 0'],
    ),
    KnownIssue(
        description='race condition in system grid custom column selection',
        failure_patterns=[
            # This first one is supposed to be fixed by 6263ae09783a43eeebeba6e18ea17326bfaf787c
            rb'show_all_columns.*NoSuchElementException:.*System-Name',
            # ... but the fix itself seems to suffer a race too?
            rb'show_all_columns.*NoSuchElementException:.*#selectablecolumns input:checked',
        ],
    ),
    KnownIssue(
        description='race condition in job matrix (dataTables_scrollHeadInner)',
        failure_patterns=[rb'test_job_matrix.*NoSuchElementException.*dataTables_scrollHeadInner'],
    ),
    KnownIssue(
        description='race condition in job matrix (StaleElementReferenceException selecting whiteboard)',
        failure_patterns=[rb'test_job_matrix.*\.select_by_visible_text\(self\.job_whiteboard\).*StaleElementReferenceException'],
    ),
    KnownIssue(
        description='OpenStack instance fails to delete with status ERROR',
        failure_patterns=[rb'dynamic_virt.*failed to delete, status ERROR'],
    ),
    KnownIssue(
        description='OpenStack instance fails to build with status BUILD',
        failure_patterns=[rb'dynamic_virt.*failed to build, status BUILD'],
    ),
    KnownIssue(
        description='OpenStack instance fails to stop with status ACTIVE',
        failure_patterns=[rb'dynamic_virt.*failed to stop, status ACTIVE'],
    ),
    KnownIssue(
        description='OpenStack quota exceeded',
        failure_patterns=[
            rb'OverQuotaClient: Quota exceeded for resources',
            rb'Error in provision_virt_recipe.*Conflict: Conflict (HTTP 409)',
            rb'Forbidden: The number of defined ports:.*is over the limit',
        ],
    ),
    KnownIssue(
        description='OpenStack Keystone 504 error',
        failure_patterns=[rb'keystoneclient.*AuthorizationFailure.*\(HTTP 504\)'],
    ),
    KnownIssue(
        description='Openstack 500 error',
        failure_patterns=[
            rb'ClientException.*\(HTTP 500\)',
        ],
    ),
    KnownIssue(
        description='UnexpectedAlertPresentException',
        failure_patterns=[rb'UnexpectedAlertPresentException'],
    ),
    KnownIssue(
        description='keystoneclient ConnectFailure',
        failure_patterns=[rb'ConnectFailure: Unable to establish connection to http:\/\/172\.16\.105\.2:35357\/v3\/OS-TRUST\/trusts'],
    ),
    KnownIssue(
        description='ENOMEM from fork()',
        failure_patterns=[rb'os\.fork\(\).*Cannot allocate memory'],
    ),
    KnownIssue(
        description='NoSuchElementException in test_secret_system',
        failure_patterns=[rb'ERROR: test_secret_system.*NoSuchElementException'],
    ),
]

def all_weeks():
    """
    When showing stats, we show number of occurrences per week starting from
    2016-W14 (earliest jobs we have) to the present. This returns a generator
    over all ISO weeks in that period.
    """
    d = datetime.date(2016, 4, 4)
    while d <= datetime.date.today():
        year, isoweek, weekday = d.isocalendar()
        yield (year, isoweek)
        d += datetime.timedelta(days=7)

def stats():
    all_jobs = []
    known_issue_occurrences = {known_issue: [] for known_issue in known_issues}
    for jobdir in dogfood_job_dirs():
        if not os.path.exists(os.path.join(jobdir, 'beaker')):
            continue
        resultsdir, = glob(os.path.join(jobdir, 'beaker', 'J:*'))
        resultsfile = os.path.join(resultsdir, 'results.xml')
        if os.path.getsize(resultsfile) == 0:
            continue # Jenkins job died while watching the Beaker job
        results = lxml.etree.parse(open(resultsfile, 'rb'))
        recipe_status, = results.xpath('/job/recipeSet/recipe/@status')
        if recipe_status not in ['Completed', 'Aborted']:
            continue
        # This is not great, but we don't have finish_time in results.xml
        timestamp = datetime.datetime.fromtimestamp(os.path.getmtime(resultsdir))
        # Test nose output against known issues
        nose_result = results.xpath('/job/recipeSet/recipe/task/results/result[@path="/distribution/beaker/dogfood/tests"]')
        if nose_result:
            result_id = nose_result[0].get('id')
            # Restraint gives resultoutputfile.log, beah gives test_log--*.
            # But we don't want to look in dmesg.log or other stuff like that.
            result_logs = nose_result[0].xpath('logs/log[@name="resultoutputfile.log" or starts-with(@name, "test_log--")]')
            if result_logs:
                nose_log_filename = os.path.join(resultsdir, '%s-%s' % (nose_result[0].get('id'), result_logs[0].get('name')))
                if os.path.exists(nose_log_filename):
                    nose_output = open(nose_log_filename, 'rb').read()
                    for known_issue in known_issues:
                        if known_issue.matches_nose_output(nose_output):
                            known_issue_occurrences[known_issue].append(timestamp)
        # Test console log against known issues
        recipe_id, = results.xpath('/job/recipeSet/recipe/@id')
        console_log_filename = os.path.join(resultsdir, '%s-console.log' % recipe_id)
        if os.path.exists(console_log_filename):
            console_output = open(console_log_filename, 'rb').read()
            for known_issue in known_issues:
                if known_issue.matches_console_output(console_output):
                    known_issue_occurrences[known_issue].append(timestamp)
        all_jobs.append(timestamp)
    for known_issue, occurrences in known_issue_occurrences.items():
        if not occurrences:
            print('WARNING: known issue %r did not match any jobs, bad pattern?' % known_issue.description, file=sys.stderr)
    return known_issue_occurrences, all_jobs

def known_issue_summary(known_issue, occurrences):
    if known_issue.bug_id:
        heading = '<h2>%s (<a href="https://bugzilla.redhat.com/show_bug.cgi?id=%s">bug %s</a>)</h2>' \
                % (known_issue.description, known_issue.bug_id, known_issue.bug_id)
    else:
        heading = '<h2>%s</h2>' % known_issue.description
    occurrences_by_week = Counter()
    for occurrence in occurrences:
        year, isoweek, weekday = occurrence.isocalendar()
        occurrences_by_week[(year, isoweek)] += 1
    table = [['Week', 'Frequency']] + [['%s-W%s' % week, occurrences_by_week[week]] for week in all_weeks()]
    return """
    <section>
        %s
        <div id="issue%s-chart" class="issue-chart" />
        <script>
            google.charts.setOnLoadCallback(function () {
                var data = google.visualization.arrayToDataTable(%s);
                var options = {
                    legend: {position: 'none'},
                };
                var chart = new google.charts.Line(document.getElementById('issue%s-chart'));
                chart.draw(data, options);
            });
        </script>
    </section>
    """ % (heading, id(known_issue), json.dumps(table), id(known_issue))

def all_issues_summary(occurrences, all_jobs):
    jobs_by_week = Counter()
    for job in all_jobs:
        year, isoweek, weekday = job.isocalendar()
        jobs_by_week[(year, isoweek)] += 1
    occurrences_by_week = Counter()
    for occurrence in occurrences:
        year, isoweek, weekday = occurrence.isocalendar()
        occurrences_by_week[(year, isoweek)] += 1
    table = [['Week', 'Affected Jobs', 'Total Jobs']] + \
            [['%s-W%s' % week, occurrences_by_week[week], jobs_by_week[week]]
             for week in all_weeks()]
    return """
    <section>
        <h2>All known issues</h2>
        <div id="all-issues-chart" />
        <script>
            google.charts.setOnLoadCallback(function () {
                var data = google.visualization.arrayToDataTable(%s);
                var options = {
                };
                var chart = new google.charts.Line(document.getElementById('all-issues-chart'));
                chart.draw(data, options);
            });
        </script>
    </section>
    """ % json.dumps(table)

def page(known_issue_occurrences, all_jobs):
    summaries = [known_issue_summary(known_issue, occurrences)
            for known_issue, occurrences
            in sorted(known_issue_occurrences.items(), key=lambda item: item[1][-1], reverse=True)]
    all_summary = all_issues_summary(sum(known_issue_occurrences.values(), []), all_jobs)
    return """
    <html>
      <head>
        <title>Dogfood known issues</title>
        <script type="text/javascript" src="https://www.gstatic.com/charts/loader.js"></script>
        <script type="text/javascript">
            google.charts.load('current', {packages: ['line']});
        </script>
        <style>
            .issue-chart { width: 1200px; height: 200px; }
            #all-issues-chart { width: 1350px; height: 300px; }
        </style>
      </head>
      <body>
        %s
        %s
	<p>Generated %s</p>
      </body>
    </html>
    """ % (all_summary, '\n'.join(summaries), datetime.datetime.utcnow().isoformat() + 'Z')

def main():
    print(page(*stats()))

if __name__ == '__main__':
    main()
