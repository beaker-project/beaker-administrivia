#!/usr/bin/python3

import os
import time
from glob import glob
import math
from collections import namedtuple
import datetime
import json
import re
import lxml.etree

max_job_age = 2 * 365 * 24 * 60 * 60 # 2 years
min_job_mtime = time.time() - max_job_age

def dogfood_job_dirs():
    el6dir = '/srv/www/jenkins-results/beaker-review-checks-dogfood-RedHatEnterpriseLinux6'
    for jobnum in os.listdir(el6dir):
        yield os.path.join(el6dir, jobnum)
    el7dir = '/srv/www/jenkins-results/beaker-review-checks-dogfood-RedHatEnterpriseLinux7'
    for jobnum in os.listdir(el7dir):
        if int(jobnum) < 49:
            continue # builds before #49 were busted
        yield os.path.join(el7dir, jobnum)

invalid_recipe_ids = [ # These are excluded from the stats to avoid skewing them
    # Xvfb was broken, skipping all WebDriver cases
    '14468',
    '14469',
    '14470',
    '14471',
    '14472',
    '14473',
    '14474',
    '14476',
    '14480',
    # pytest patch broke the tests for an unknown reason
    '13652',
    # tests hung for unknown reason, hit LWD
    '15355',
    # LWD due to wrong value of BEAKER_SERVER_BASE_URL
    '16081',
    '16084',
    '16085',
    '16088',
    '16089',
    '16090',
    '16091',
    '16097',
    # reservesys skewed the recipe duration very high
    '17382',
    # distro attributes in results.xml are "None" instead of proper values,
    # which mucks up the graph's legend
    # https://bugzilla.redhat.com/show_bug.cgi?id=911515#c33
    '20559',
    '20562',
    '20563',
    '20565',
    '20603',
    '20609',
    '20612',
    '20611',
]
invalid_job_whiteboard_patterns = [
    # buggy patch which caused the tests to time out
    'beaker dogfood .* for Gerrit 5860/',
]

def hostname_to_group(hostname):
    """
    Some hosts are basically identical so we group them together to make the
    stats more meaningful.
    """
    if hostname.startswith('host-192-168-') or hostname.startswith('beaker-recipe-'):
        return 'OpenStack'
    hostname = hostname.split('.')[0]
    if hostname.startswith('dev-kvm-guest-'):
        # These all have matching specs and are hosted on the same host.
        return 'dev-kvm-guest-*'
    if hostname in ['ibm-x3250m4-18', 'ibm-x3250m4-19']:
        # Two identical machines
        return 'ibm-x3250m4-*'
    if hostname in ['hp-dl120gen9-06', 'hp-dl160gen9-04']:
        # These two are not identical but their performance is
        return 'hp-dl*gen9-*'
    return hostname

def parse_beaker_duration(duration_text):
    # "02:01:00"
    hours, minutes, seconds = duration_text.split(':')
    return datetime.timedelta(seconds=(int(hours) * 3600 + int(minutes) * 60 + int(seconds)))

def log_filename_for_result(resultsxml, resultsdir, result_name): # -> filename or None if it doesn't exist
    result = resultsxml.xpath('/job/recipeSet/recipe/task/results/result[@path="%s"]' % result_name)
    if not result:
        return None
    # Restraint gives resultoutputfile.log, beah gives test_log--*.
    result_logs = result[0].xpath('logs/log[@name="resultoutputfile.log" or starts-with(@name, "test_log--")]')
    if not result_logs:
        return None
    filename = os.path.join(resultsdir, '%s-%s' % (result[0].get('id'), result_logs[0].get('name')))
    if not os.path.exists(filename):
        return None
    return filename

def stats():
    rowtype = namedtuple('Row', ['timestamp', 'hours_ran', 'recipeid', 'hostgroup', 'hostname'])
    rows = []
    for jobdir in dogfood_job_dirs():
        if os.path.getmtime(jobdir) < min_job_mtime:
            continue
        if not os.path.exists(os.path.join(jobdir, 'beaker')):
            continue
        resultsdir, = glob(os.path.join(jobdir, 'beaker', 'J:*'))
        results = lxml.etree.parse(open(os.path.join(resultsdir, 'results.xml'), 'rb'))
        sysinfo_log_filename = log_filename_for_result(results, resultsdir, '/distribution/install/Sysinfo')
        if not sysinfo_log_filename:
            continue
        hostname_match = re.search(r'Hostname                = (.*)$', open(sysinfo_log_filename).read(), re.M)
        if not hostname_match:
            raise ValueError('Log %s does not contain hostname' % sysinfo_log_filename)
        hostname = hostname_match.group(1)
        hostgroup = hostname_to_group(hostname)
        job_whiteboard = results.xpath('/job/whiteboard/text()')[0].strip()
        if any(re.match(p, job_whiteboard) for p in invalid_job_whiteboard_patterns):
            continue
        recipeid, = results.xpath('/job/recipeSet/recipe/@id')
        if recipeid in invalid_recipe_ids:
            continue
        family, = results.xpath('/job/recipeSet/recipe/@family')
        family = family.replace('RedHatEnterpriseLinux', 'RHEL')
        hostgroup = '%s[%s]' % (hostgroup, family)
        recipe_status, = results.xpath('/job/recipeSet/recipe/@status')
        if recipe_status != 'Completed':
            continue
        setup_result, = results.xpath('/job/recipeSet/recipe/task[@name="/distribution/beaker/setup"]/@result')
        if setup_result != 'Pass':
            continue # tests are likely invalid
        nose_log_filename = log_filename_for_result(results, resultsdir, '/distribution/beaker/dogfood/tests')
        if not nose_log_filename:
            continue
        test_count_match = re.search(r'^Ran (\d+) tests in .*s$', open(nose_log_filename).read(), re.M)
        if not test_count_match:
            continue
        test_count = int(test_count_match.group(1))
        if test_count < 1000:
            continue
        duration_text, = results.xpath('/job/recipeSet/recipe/@duration')
        duration = parse_beaker_duration(duration_text)
        hours_ran = duration.total_seconds() / 3600.
        # This is not great, but we don't have finish_time in results.xml
        timestamp = datetime.datetime.fromtimestamp(os.path.getmtime(resultsdir))
        rows.append(rowtype(timestamp, hours_ran, recipeid, hostgroup, hostname))
    rows = sorted(rows, key=lambda r: r.timestamp)
    all_hostgroups = sorted(set(row.hostgroup for row in rows))
    averages_by_row = {}
    upper_variances_by_row = {}
    lower_variances_by_row = {}
    for hostgroup in all_hostgroups:
        hostrows = [row for row in rows if row.hostgroup == hostgroup]
        # compute centred exponential weighted mean and variance for each point except the edge-most ones
        # http://tdunning.blogspot.com.au/2011/03/exponential-weighted-averages-with.html
        # http://nfs-uxsup.csx.cam.ac.uk/~fanf2/hermes/doc/antiforgery/stats.pdf
        alpha = 5 # smoothing factor
        for i, row in enumerate(hostrows):
            if i < 3 or i > len(hostrows) - 3:
                continue
            weights = [math.exp(-(abs((row.timestamp - other_row.timestamp).total_seconds()) / (24*60*60)) / alpha)
                    for other_row in hostrows]
            average = (
                sum(weight * other_row.hours_ran
                    for other_row, weight in zip(hostrows, weights))
              / sum(weights))
            averages_by_row[row] = average
            upper_variances_by_row[row] = (
                sum(weight * (other_row.hours_ran - average)**2
                    for other_row, weight in zip(hostrows, weights)
                    if other_row.hours_ran > average)
              / sum(weights))
            lower_variances_by_row[row] = (
                sum(weight * (other_row.hours_ran - average)**2
                    for other_row, weight in zip(hostrows, weights)
                    if other_row.hours_ran <= average)
              / sum(weights))
    google_cols = [
        {'id': 'finished', 'type': 'datetime'},
        {'id': 'hours_ran', 'type': 'number'},
        {'id': 'tooltip', 'type': 'string', 'role': 'tooltip'},
    ]
    for hostgroup in all_hostgroups:
        google_cols.extend([
            {'id': 'hours_ran_rolling_avg_%s' % hostgroup, 'type': 'number', 'label': hostgroup},
            {'id': 'hours_ran_interval_high_%s' % hostgroup, 'type': 'number', 'role': 'interval'},
            {'id': 'hours_ran_interval_low_%s' % hostgroup, 'type': 'number', 'role': 'interval'},
        ])
    google_rows = []
    for row in rows:
        google_row = [
            {'v': row.timestamp},
            {'v': row.hours_ran},
            {'v': 'R:%s on %s' % (row.recipeid, row.hostname)},
        ]
        for hostgroup in all_hostgroups:
            if row.hostgroup != hostgroup or row not in averages_by_row:
                google_row.extend([
                    {'v': None},
                    {'v': None},
                    {'v': None},
                ])
            else:
                google_row.extend([
                    {'v': averages_by_row[row]},
                    {'v': averages_by_row[row] + math.sqrt(upper_variances_by_row[row])},
                    {'v': averages_by_row[row] - math.sqrt(lower_variances_by_row[row])},
                ])
        google_rows.append({'c': google_row})
    return {'cols': google_cols, 'rows': google_rows}

class JSONEncoderWithDate(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, datetime.datetime):
            # Google Visualization format for dates in JSON
            return 'Date(%d,%d,%d,%d,%d,%d)' % (o.year, o.month - 1, o.day,
                    o.hour, o.minute, o.second)
        else:
            raise TypeError()

def page(table):
    return """
    <html>
      <head>
        <title>Dogfood jobs: running time by host</title>
        <script type="text/javascript" src="https://www.google.com/jsapi"></script>
        <script type="text/javascript">
          google.load("visualization", "1", {packages:["corechart"]});
          google.setOnLoadCallback(drawChart);
          function drawChart() {
            window.data = new google.visualization.DataTable(%s);
            var options = {
              title: 'Dogfood jobs: running time by host',
              hAxis: {title: 'Finished', viewWindowMode: 'maximized'},
              vAxis: {title: 'Hours ran'},
              chartArea: {left: 75, width: '75%%', height: '70%%'},
              legend: {'position': 'right'},
              tooltip: {isHtml: true},
              explorer: {},
              intervals: {style: 'area'},
              interpolateNulls: true,
              lineWidth: 3,
              series: {
                0: { // scatter points
                  pointSize: 3,
                  lineWidth: 0,
                },
              },
            };
            var chart = new google.visualization.LineChart(document.getElementById('chart'));
            chart.draw(data, options);
          }
        </script>
      </head>
      <body>
        <div id="chart" style="width: 1400px; height: 800px;"></div>
        <p>Line shows rolling weighted average, with 1 std. dev. interval</p>
	<p>Generated %s</p>
      </body>
    </html>
    """ % (JSONEncoderWithDate().encode(table), datetime.datetime.utcnow().isoformat() + 'Z')

def main():
    print(page(stats()))

if __name__ == '__main__':
    main()
