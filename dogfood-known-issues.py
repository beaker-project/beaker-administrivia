#!/usr/bin/python

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

    def matches_nose_output(self, filename):
        if not self.failure_patterns:
            return False
        failures = re.split(r'={70}\n|-{70}\nRan ', open(filename).read())[1:-1]
        for failure in failures:
            for failure_pattern in self.failure_patterns:
                if failure_pattern.search(failure):
                    return True
        return False

    def matches_console_output(self, filename):
        if not self.console_patterns:
            return False
        console = open(filename).read()
        for console_pattern in self.console_patterns:
            if console_pattern.search(console):
                return True
        return False

known_issues = [
    KnownIssue(
        description='WebDriverException: Message: Can\'t load the profile',
        failure_patterns=[r'WebDriverException: Message: Can\'t load the profile\.'],
    ),
    KnownIssue(
        description='WebDriver Connection refused',
        failure_patterns=[r'webdriver\.Firefox\(.*create_connection.*Connection refused'],
    ),
    KnownIssue(
        description='/boot corrupted',
        console_patterns=[
            # error: not a correct XFS inode.
            r'e\s*r\s*r\s*o\s*r\s*:\s+n\s*o\s*t\s+a\s+c\s*o\s*r\s*r\s*e\s*c\s*t\s+X\s*F\s*S\s+i\s*n\s*o\s*d\s*e\s*\.',
            # error: attempt to read or write outside of partition.
            r'e\s*r\s*r\s*o\s*r\s*:\s+a\s*t\s*t\s*e\s*m\s*p\s*t\s+t\s*o\s+r\s*e\s*a\s*d\s+o\s*r\s+w\s*r\s*i\s*t\s*e\s+o\s*u\s*t\s*s\s*i\s*d\s*e\s+o\s*f\s+p\s*a\s*r\s*t\s*i\s*t\s*i\s*o\s*n\s*\.',
            # alloc magic is broken at 0x
            r'a\s*l\s*l\s*o\s*c\s+m\s*a\s*g\s*i\s*c\s+i\s*s\s+b\s*r\s*o\s*k\s*e\s*n\s+a\s*t\s+0\s*x',
            # error: file `/grub2/i386-pc/bufio.mod' not found.
            r'e\s*r\s*r\s*o\s*r\s*:\s+f\s*i\s*l\s*e\s+`.*\.\s*m\s*o\s*d\s*\'\s+n\s*o\s*t\s+f\s*o\s*u\s*n\s*d\s*\.',
        ],
    ),
    KnownIssue(
        # fixed in d3f70b5947b6927460ebed0a580fa2efe9bfd746
        description='dogfood tests can fail because beaker-provision is trying to use fence_ilo to power on a non-existent machine',
        bug_id='1336272',
        failure_patterns=[
            r'self\.assertEqual\(activity_count \+ 1, Activity\.query\.count\(\)\)\nAssertionError:',
            r'command\.change_status\(CommandStatus\.aborted\).*StaleCommandStatusException:',
        ],
    ),
    KnownIssue(
        # fixed in 74aba3513b1126002cf885e6bf48ff463a16dd3b
        description='race condition with recipe page refresh',
        failure_patterns=[r'test_page_updates_itself_while_recipe_is_running.*StaleElementReferenceException:'],
    ),
    KnownIssue(
        # fixed in bc3a8af2ec6aa9a5f354629b60579606840f08cb
        description='race condition in reserve workflow tree selection',
        failure_patterns=[r'self\.assert_\(not any\(\'i386\' in option\.text for option in options\), options\).*StaleElementReferenceException:'],
    ),
    KnownIssue(
        # fixed in 6263ae09783a43eeebeba6e18ea17326bfaf787c
        description='race condition in system grid custom column selection',
        failure_patterns=[r'show_all_columns.*NoSuchElementException:.*System-Name'],
    ),
    KnownIssue(
        # fixed in 70d8e8d472ab7e95fbf4a18ef61ee209d27a5f34
        description='race condition in test_html_in_comments_is_escaped',
        failure_patterns=[r'test_html_in_comments_is_escaped.*AssertionError: u\'\' != \'<script>alert\("xss"\)</script>\''],
    ),
    KnownIssue(
        description='timeout in test_quiescent_period_only_applies_between_power_commands is too aggressive',
        failure_patterns=[r'test_quiescent_period_only_applies_between_power_commands.*wait_for_command_to_finish\(commands\[1\]'],
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
        results = lxml.etree.parse(open(os.path.join(resultsdir, 'results.xml'), 'rb'))
        recipe_status, = results.xpath('/job/recipeSet/recipe/@status')
        if recipe_status not in ['Completed', 'Aborted']:
            continue
        testsuite_logs = glob(os.path.join(resultsdir, '*-test_log--distribution-beaker-dogfood-tests.log'))
        console_logs = glob(os.path.join(resultsdir, '*-console.log'))
        # This is not great, but we don't have finish_time in results.xml
        timestamp = datetime.datetime.fromtimestamp(os.path.getmtime(resultsdir))
        for known_issue in known_issues:
            if testsuite_logs and known_issue.matches_nose_output(testsuite_logs[0]):
                known_issue_occurrences[known_issue].append(timestamp)
            if console_logs and known_issue.matches_console_output(console_logs[0]):
                known_issue_occurrences[known_issue].append(timestamp)
        all_jobs.append(timestamp)
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
            in sorted(known_issue_occurrences.iteritems(), key=lambda (k, o): o[-1], reverse=True)]
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
    print page(*stats())

if __name__ == '__main__':
    main()
