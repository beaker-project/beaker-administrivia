#!/usr/bin/python

import os
from glob import glob
import math
from collections import namedtuple
import datetime
import json
import re
import lxml.etree

DOGFOOD_RESULTS_BASEDIR = '/srv/www/jenkins-results/beaker-review-checks-dogfood-RedHatEnterpriseLinux6'

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
    rowtype = namedtuple('Row', ['timestamp', 'hours_ran', 'recipeid', 'hostname'])
    rows = []
    for jobnum in os.listdir(DOGFOOD_RESULTS_BASEDIR):
        jobdir = os.path.join(DOGFOOD_RESULTS_BASEDIR, jobnum)
        if not os.path.exists(os.path.join(jobdir, 'beaker')):
            continue
        resultsdir, = glob(os.path.join(jobdir, 'beaker', 'J:*'))
        logs_containing_hostname = glob(os.path.join(resultsdir, '*-test_log-Install-Beaker-server.log'))
        if not logs_containing_hostname:
            continue
        hostname = re.search(r'Hostname      : (.*)$', open(logs_containing_hostname[0]).read(), re.M).group(1)
        hostname = hostname.split('.')[0]
        hostname = hostname_to_group(hostname) # Not really a hostname anymore but oh well
        results = lxml.etree.parse(open(os.path.join(resultsdir, 'results.xml'), 'rb'))
        recipeid, = results.xpath('/job/recipeSet/recipe/@id')
        duration_text, = results.xpath('/job/recipeSet/recipe/task[@name="/distribution/beaker/dogfood"]/@duration')
        duration = parse_beaker_duration(duration_text)
        hours_ran = duration.total_seconds() / 3600.
        if hours_ran < 1.5:
            # Not likely that it ran any tests
            continue
        # This is not great, but we don't have finish_time in results.xml
        timestamp = datetime.datetime.fromtimestamp(os.path.getmtime(resultsdir))
        rows.append(rowtype(timestamp, hours_ran, recipeid, hostname))
    rows = sorted(rows, key=lambda r: r.timestamp)
    all_hostnames = sorted(set(row.hostname for row in rows))
    averages_by_row = {}
    upper_variances_by_row = {}
    lower_variances_by_row = {}
    for hostname in all_hostnames:
        hostrows = [row for row in rows if row.hostname == hostname]
        # compute centred exponential weighted mean and variance for each point except the edge-most ones
        # http://tdunning.blogspot.com.au/2011/03/exponential-weighted-averages-with.html
        # http://nfs-uxsup.csx.cam.ac.uk/~fanf2/hermes/doc/antiforgery/stats.pdf
        alpha = 3 # smoothing factor
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
    for hostname in all_hostnames:
        google_cols.extend([
            {'id': 'hours_ran_rolling_avg_%s' % hostname, 'type': 'number', 'label': hostname},
            {'id': 'hours_ran_interval_high_%s' % hostname, 'type': 'number', 'role': 'interval'},
            {'id': 'hours_ran_interval_low_%s' % hostname, 'type': 'number', 'role': 'interval'},
        ])
    google_rows = []
    for row in rows:
        google_row = [
            {'v': row.timestamp},
            {'v': row.hours_ran},
            {'v': 'R:%s on %s' % (row.recipeid, row.hostname)},
        ]
        for hostname in all_hostnames:
            if row.hostname != hostname or row not in averages_by_row:
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
