#!/usr/bin/python

import datetime
import json
import requests

GERRIT_CHANGES_URL = 'http://gerrit.beaker-project.org/changes/?q=project:beaker&o=ALL_REVISIONS&o=MESSAGES&o=DETAILED_ACCOUNTS&n=500'
NON_HUMAN_REVIEWERS = ['jenkins']
POSTED_SINCE = datetime.datetime.utcnow() - datetime.timedelta(days=365)

def parse_gerrit_timestamp(timestamp):
    # "2015-09-08 04:39:30.493000000"
    return datetime.datetime.strptime(timestamp[:19], '%Y-%m-%d %H:%M:%S')

def stats(changes):
    cols = [
        {'id': 'posted', 'type': 'datetime'},
        {'id': 'days_to_first_review', 'type': 'number'},
        {'id': 'tooltip', 'type': 'string', 'role': 'tooltip'},
    ]
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
                        and message['author']['_account_id'] != change['owner']['_account_id']
                        and message['author'].get('username') not in NON_HUMAN_REVIEWERS]
            if not review_times:
                continue
            time_to_first_review = min(review_times) - posted_time
            rows.append({'c': [
                {'v': posted_time},
                {'v': time_to_first_review.total_seconds() / (24*60*60)},
                {'v': 'Gerrit change %s patch %s' % (change['_number'], revision['_number'])},
            ]})
    return {'cols': cols, 'rows': rows}

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
            };
            var chart = new google.visualization.ScatterChart(document.getElementById('chart'));
            chart.draw(data, options);
          }
        </script>
      </head>
      <body>
        <div id="chart" style="width: 1400px; height: 800px;"></div>
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
