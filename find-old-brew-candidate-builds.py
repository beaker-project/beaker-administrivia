#!/usr/bin/python2

"""
Finds builds in *-candidate tags where a newer (higher EVR) build is being 
inherited from a parent tag. This is usually an error (means the older build 
did not pass testing and should just be untagged and deleted).
"""

import os, os.path
from ConfigParser import SafeConfigParser
import rpm
import koji
import xmlrpclib

koji_config = SafeConfigParser()
koji_config.read(['/etc/brewkoji.conf', '/etc/koji.conf',
        os.path.expanduser('~/.koji/config')])
hub_url = koji_config.get('brew', 'server')

koji_session = koji.ClientSession(hub_url)
tags = [
    'beaker-server-rhel-6-candidate',
    'beaker-server-rhel-7-candidate',
    'beaker-harness-rhel-5-candidate',
    'beaker-harness-rhel-6-candidate',
    'beaker-harness-rhel-7-candidate',
]

koji_session.multicall = True
for tag in tags:
    koji_session.listTagged(tag, inherit=False)
    koji_session.listTagged(tag, inherit=True)
results = koji_session.multiCall()
koji_session.multicall = False

for tag in tags:
    result = results.pop(0)
    if 'faultCode' in result:
        raise xmlrpclib.Fault(result['faultCode'], result['faultString'])
    builds_in_testing, = result
    result = results.pop(0)
    if 'faultCode' in result:
        raise xmlrpclib.Fault(result['faultCode'], result['faultString'])
    all_builds, = result
    for testing_build in sorted(builds_in_testing, key=lambda b: b['package_name']):
        for build in all_builds:
            if (testing_build['tag_name'] != build['tag_name'] and
                    testing_build['package_name'] == build['package_name'] and
                    rpm.labelCompare(
                        (testing_build['epoch'], testing_build['version'], testing_build['release']),
                        (build['epoch'], build['version'], build['release']))
                    < 0):
                # The build in testing is older than some other inherited build.
                print '%s-%s-%s (%s) < %s-%s-%s (%s)' % (
                    testing_build['package_name'],
                    testing_build['version'],
                    testing_build['release'],
                    tag,
                    build['package_name'],
                    build['version'],
                    build['release'],
                    build['tag_name'])
                print '    koji -p brew untag-pkg %s %s-%s-%s' % (
                    tag,
                    testing_build['package_name'],
                    testing_build['version'],
                    testing_build['release'])
                break
