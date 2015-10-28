#!/usr/bin/python

import math
from collections import namedtuple
import datetime
import json
import requests

GERRIT_CHANGES_URL = 'http://gerrit.beaker-project.org/changes/?q=project:beaker&o=ALL_REVISIONS&o=MESSAGES&o=DETAILED_ACCOUNTS&n=500'
NON_HUMAN_REVIEWERS = ['patchbot', 'jenkins']
POSTED_SINCE = datetime.datetime.utcnow() - datetime.timedelta(days=365)

def parse_gerrit_timestamp(timestamp):
    # "2015-09-08 04:39:30.493000000"
    return datetime.datetime.strptime(timestamp[:19], '%Y-%m-%d %H:%M:%S')

def stats(changes):
    rowtype = namedtuple('Row', ['posted_time', 'days_to_first_review', 'revision', 'change'])
    rows = []
    for change in changes:
        for revision in change['revisions'].itervalues():
            posted_time = parse_gerrit_timestamp(revision['created'])
            if posted_time < POSTED_SINCE:
                continue
            review_times = [parse_gerrit_timestamp(message['date'])
                    for message in change['messages']
                    if message['_revision_number'] == revision['_number']
                        and 'author' in message
                        and message['author']['_account_id'] != revision['uploader']['_account_id']
                        and message['author'].get('username') not in NON_HUMAN_REVIEWERS]
            if not review_times:
                continue
            time_to_first_review = min(review_times) - posted_time
            days_to_first_review = time_to_first_review.total_seconds() / (24*60*60)
            rows.append(rowtype(posted_time, days_to_first_review, revision, change))
    rows = sorted(rows, key=lambda r: r.posted_time)
    # compute centred exponential weighted mean and variance for each point except the edge-most ones
    # http://tdunning.blogspot.com.au/2011/03/exponential-weighted-averages-with.html
    # http://nfs-uxsup.csx.cam.ac.uk/~fanf2/hermes/doc/antiforgery/stats.pdf
    alpha = 5 # smoothing factor
    averages = []
    upper_variances = []
    lower_variances = []
    for i, row in enumerate(rows):
        if i < 5 or i > len(rows) - 5:
            averages.append(None)
            upper_variances.append(None)
            lower_variances.append(None)
            continue
        weights = [math.exp(-(abs((row.posted_time - other_row.posted_time).total_seconds()) / (24*60*60)) / alpha)
                for other_row in rows]
        average = (
            sum(weight * other_row.days_to_first_review
                for other_row, weight in zip(rows, weights))
          / sum(weights))
        averages.append(average)
        upper_variances.append(
            sum(weight * (other_row.days_to_first_review - average)**2
                for other_row, weight in zip(rows, weights)
                if other_row.days_to_first_review > average)
          / sum(weights))
        lower_variances.append(
            sum(weight * (other_row.days_to_first_review - average)**2
                for other_row, weight in zip(rows, weights)
                if other_row.days_to_first_review <= average)
          / sum(weights))
    return {'cols': [
        {'id': 'posted', 'type': 'datetime'},
        {'id': 'days_to_first_review', 'type': 'number'},
        {'id': 'tooltip', 'type': 'string', 'role': 'tooltip'},
        {'id': 'days_to_first_review_rolling_avg', 'type': 'number'},
        {'id': 'days_to_first_review_interval_high', 'type': 'number', 'role': 'interval'},
        {'id': 'days_to_first_review_interval_low', 'type': 'number', 'role': 'interval'},
    ], 'rows': [
        {'c': [
            {'v': row.posted_time},
            {'v': row.days_to_first_review},
            {'v': 'Gerrit change %s patch %s' % (row.change['_number'], row.revision['_number'])},
            {'v': averages[i]},
            {'v': averages[i] + math.sqrt(upper_variances[i]) if averages[i] is not None else None},
            {'v': averages[i] - math.sqrt(lower_variances[i]) if averages[i] is not None else None},
        ]} for i, row in enumerate(rows)]}

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
        <script type="text/javascript" src="https://www.google.com/jsapi"></script>
        <script type="text/javascript">
          google.load("visualization", "1", {packages:["corechart"]});
          google.setOnLoadCallback(drawChart);
          function drawChart() {
            window.data = new google.visualization.DataTable(%s);
            var options = {
              title: 'Gerrit patch sets: time to first review',
              hAxis: {title: 'Posted', viewWindowMode: 'maximized'},
              vAxis: {title: 'Days to first review', logScale: true},
              legend: 'none',
              tooltip: {isHtml: true},
              explorer: {},
              intervals: {style: 'area'},
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
    response = requests.get(GERRIT_CHANGES_URL)
    response.raise_for_status()
    # need to strip Gerrit's anti-XSSI prefix from response body
    changes = json.loads(response.text.lstrip(")]}'"))
    print page(stats(changes))

if __name__ == '__main__':
    main()