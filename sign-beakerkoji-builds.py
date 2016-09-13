#!/bin/bash
set -e

if [ "$#" -lt 1 ] ; then
    echo "Usage: $0 <nvr>..." >&2
    echo "Example: $0 beaker-23.2-1.el7_2" >&2
    exit 1
fi

workdir=$(mktemp -d -t sign-beakerkoji-build-workdir.XXXXXXXXXX)
pushd "$workdir"
function cleanup {
    popd
    rm -rf "$workdir"
}
trap cleanup EXIT

for nvr in "$@" ; do
    koji -p beakerkoji download-build --debuginfo "$nvr"
done
rpmsign --key-id=87CD4C3C3A43A632E0E71BE822B0AAAF4DF16B33 --addsign *.rpm
koji -p beakerkoji import-sig *.rpm
for nvr in "$@" ; do
    koji -p beakerkoji write-signed-rpm 4df16b33 "$nvr"
done
