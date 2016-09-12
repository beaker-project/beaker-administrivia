#!/bin/bash
set -e

if [ "$#" -lt 3 ] ; then
    echo "Usage: $0 <source-tag-in-fedora> <destination-tag-in-beakerkoji> <package-name>..." >&2
    echo "Example: $0 epel7 beaker-server-rhel-7-testing python-novaclient python-keystoneclient" >&2
    exit 1
fi

srctag="$1"
shift
desttag="$1"
shift

workdir=$(mktemp -d -t import-epel-to-beakerkoji-workdir.XXXXXXXXXX)
pushd "$workdir"
function cleanup {
    popd
    rm -rf "$workdir"
}
trap cleanup EXIT

for package in "$@" ; do
    koji download-build --debuginfo --latestfrom="$srctag" "$package"
done
koji -p beakerkoji import *.src.rpm
koji -p beakerkoji import *.rpm
rpmsign --key-id=87CD4C3C3A43A632E0E71BE822B0AAAF4DF16B33 --addsign *.rpm
koji -p beakerkoji import-sig *.rpm
for nvr in $(rpm -q --qf '%{name}-%{version}-%{release} ' -p *.src.rpm) ; do
    koji -p beakerkoji write-signed-rpm 4df16b33 "$nvr"
    koji -p beakerkoji tag-pkg --force "$desttag" "$nvr"
done
