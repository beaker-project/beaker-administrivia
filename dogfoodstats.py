#!/usr/bin/python

import os
from glob import glob
import math
from collections import namedtuple
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
]

def hostname_to_group(hostname):
    """
    Some hosts are basically identical so we group them together to make the
    stats more meaningful.
    """
    if hostname.startswith('dev-kvm-guest-'):
        # These all have matching specs and are hosted on the same host.
        return 'dev-kvm-guest-*'
    if hostname in ['ibm-x3250m4-18', 'ibm-x3250m4-19']:
        # Two identical machines
        return 'ibm-x3250m4-*'
    return hostname

def parse_beaker_duration(duration_text):
    # "02:01:00"
    hours, minutes, seconds = duration_text.split(':')
    return datetime.timedelta(seconds=(int(hours) * 3600 + int(minutes) * 60 + int(seconds)))

def stats():
    rowtype = namedtuple('Row', ['timestamp', 'hours_ran', 'recipeid', 'hostgroup', 'hostname'])
    rows = []
    for jobdir in dogfood_job_dirs():
        if not os.path.exists(os.path.join(jobdir, 'beaker')):
            continue
        resultsdir, = glob(os.path.join(jobdir, 'beaker', 'J:*'))
        logs_containing_hostname = glob(os.path.join(resultsdir, '*-test_log-Install-Beaker-server.log'))
        if not logs_containing_hostname:
            continue
        hostname = re.search(r'Hostname      : (.*)$', open(logs_containing_hostname[0]).read(), re.M).group(1)
        hostname = hostname.split('.')[0]
        hostgroup = hostname_to_group(hostname)
        results = lxml.etree.parse(open(os.path.join(resultsdir, 'results.xml'), 'rb'))
        recipeid, = results.xpath('/job/recipeSet/recipe/@id')
        if recipeid in invalid_recipe_ids:
            continue
        family, = results.xpath('/job/recipeSet/recipe/@family')
        family = family.replace('RedHatEnterpriseLinux', 'RHEL')
        hostgroup = '%s[%s]' % (hostgroup, family)
        setup_result, = results.xpath('/job/recipeSet/recipe/task[@name="/distribution/beaker/setup"]/@result')
        if setup_result != 'Pass':
            continue # tests are likely invalid
        duration_text, = results.xpath('/job/recipeSet/recipe/task[@name="/distribution/beaker/dogfood"]/@duration')
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
    print page(stats())

if __name__ == '__main__':
    main()
