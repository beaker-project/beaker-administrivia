#!/bin/bash
set -ex

usage() {
    echo "Sets up Koji tags and build targets for a new Fedora release" >&2
    echo "Usage: $0 <release>, example: $0 23" >&2
    exit 1
}
[[ -z "$REL" ]] && REL="$1"
[[ -z "$REL" ]] && usage

# Normally it would be -everything, but -development if we are adding it before 
# there has been an official release synced to the mirror, which we usually do.
EXTERNAL_REPO_NAME="fedora-$REL-development"

if [ "$(koji -p beakerkoji list-external-repos --name $EXTERNAL_REPO_NAME --quiet | wc -l)" -ne 1 ] ; then
    echo "You forgot to define an external repo named $EXTERNAL_REPO_NAME" >&2
    exit 1
fi

koji -p beakerkoji add-tag beaker-client-fedora-$REL
koji -p beakerkoji add-tag beaker-client-fedora-$REL-testing --parent beaker-client-fedora-$REL
koji -p beakerkoji add-tag beaker-client-fedora-$REL-redhat --parent beaker-client-fedora-$REL
koji -p beakerkoji add-tag beaker-client-fedora-$REL-redhat-testing --parent beaker-client-fedora-$REL-testing --parent beaker-client-fedora-$REL
koji -p beakerkoji add-tag beaker-client-fedora-$REL-build --parent beaker-client-fedora-$REL --arches=i686,x86_64
koji -p beakerkoji add-target beaker-client-fedora-$REL-testing beaker-client-fedora-$REL-build beaker-client-fedora-$REL-testing
koji -p beakerkoji add-target beaker-client-fedora-$REL-redhat-testing beaker-client-fedora-$REL-build beaker-client-fedora-$REL-redhat-testing
koji -p beakerkoji add-group beaker-client-fedora-$REL-build build
koji -p beakerkoji add-group-pkg beaker-client-fedora-$REL-build build bash bzip2 coreutils cpio diffutils fedora-release findutils gawk gcc gcc-c++ grep gzip info make patch redhat-rpm-config rpm-build sed shadow-utils tar unzip util-linux which xz
koji -p beakerkoji add-group beaker-client-fedora-$REL-build srpm-build
koji -p beakerkoji add-group-pkg beaker-client-fedora-$REL-build srpm-build bash fedora-release git redhat-rpm-config rhpkg-simple fedpkg-minimal rpm-build shadow-utils
koji -p beakerkoji add-external-repo --tag beaker-client-fedora-$REL-build $EXTERNAL_REPO_NAME
koji -p beakerkoji add-pkg --owner=$USER beaker-client-fedora-$REL beaker rhts
koji -p beakerkoji add-pkg --owner=$USER beaker-client-fedora-$REL-redhat beaker-redhat beaker-redhat-repo beakerlib-redhat
koji -p beakerkoji add-pkg --owner=$USER beaker-client-fedora-$REL-build rhpkg-simple
koji -p beakerkoji tag-pkg --nowait beaker-client-fedora-$REL-build rhpkg-simple-1.8-1.el7

koji -p beakerkoji add-tag beaker-harness-fedora-$REL
koji -p beakerkoji add-tag beaker-harness-fedora-$REL-testing --parent beaker-harness-fedora-$REL
koji -p beakerkoji add-tag beaker-harness-fedora-$REL-redhat --parent beaker-harness-fedora-$REL
koji -p beakerkoji add-tag beaker-harness-fedora-$REL-redhat-testing --parent beaker-harness-fedora-$REL-testing --parent beaker-harness-fedora-$REL
koji -p beakerkoji add-tag beaker-harness-fedora-$REL-build --parent beaker-harness-fedora-$REL --arches=i686,x86_64
koji -p beakerkoji add-target beaker-harness-fedora-$REL beaker-harness-fedora-$REL-build beaker-harness-fedora-$REL
koji -p beakerkoji add-target beaker-harness-fedora-$REL-testing beaker-harness-fedora-$REL-build beaker-harness-fedora-$REL-testing
koji -p beakerkoji add-target beaker-harness-fedora-$REL-redhat beaker-harness-fedora-$REL-build beaker-harness-fedora-$REL-redhat
koji -p beakerkoji add-target beaker-harness-fedora-$REL-redhat-testing beaker-harness-fedora-$REL-build beaker-harness-fedora-$REL-redhat-testing
koji -p beakerkoji add-group beaker-harness-fedora-$REL-build build
koji -p beakerkoji add-group-pkg beaker-harness-fedora-$REL-build build bash bzip2 coreutils cpio diffutils fedora-release findutils gawk gcc gcc-c++ grep gzip info make patch redhat-rpm-config rpm-build sed shadow-utils tar unzip util-linux which xz
koji -p beakerkoji add-group beaker-harness-fedora-$REL-build srpm-build
koji -p beakerkoji add-group-pkg beaker-harness-fedora-$REL-build srpm-build bash fedora-release git redhat-rpm-config rhpkg-simple fedpkg-minimal rpm-build shadow-utils
koji -p beakerkoji add-external-repo --tag beaker-harness-fedora-$REL-build $EXTERNAL_REPO_NAME
koji -p beakerkoji add-pkg --owner=$USER beaker-harness-fedora-$REL beah rhts lshw beaker-system-scan restraint staf
koji -p beakerkoji add-pkg --owner=$USER beaker-harness-fedora-$REL-redhat beakerlib-redhat
koji -p beakerkoji add-pkg --owner=$USER beaker-harness-fedora-$REL-build rhpkg-simple
koji -p beakerkoji tag-pkg --nowait beaker-harness-fedora-$REL-build rhpkg-simple-1.8-1.el7

koji -p beakerkoji add-tag beaker-server-fedora-$REL
koji -p beakerkoji add-tag beaker-server-fedora-$REL-testing --parent beaker-server-fedora-$REL
koji -p beakerkoji add-tag beaker-server-fedora-$REL-build --parent beaker-server-fedora-$REL --arches=x86_64
koji -p beakerkoji add-target beaker-server-fedora-$REL-testing beaker-server-fedora-$REL-build beaker-server-fedora-$REL-testing
koji -p beakerkoji add-group beaker-server-fedora-$REL-build build
koji -p beakerkoji add-group-pkg beaker-server-fedora-$REL-build build bash bzip2 coreutils cpio diffutils fedora-release findutils gawk gcc gcc-c++ grep gzip info make patch redhat-rpm-config rpm-build sed shadow-utils tar unzip util-linux which xz
koji -p beakerkoji add-group beaker-server-fedora-$REL-build srpm-build
koji -p beakerkoji add-group-pkg beaker-server-fedora-$REL-build srpm-build bash fedora-release git redhat-rpm-config rhpkg-simple fedpkg-minimal rpm-build shadow-utils
koji -p beakerkoji add-external-repo --tag beaker-server-fedora-$REL-build $EXTERNAL_REPO_NAME
koji -p beakerkoji add-pkg --owner=$USER beaker-server-fedora-$REL beaker
koji -p beakerkoji add-pkg --owner=$USER beaker-server-fedora-$REL-build rhpkg-simple
koji -p beakerkoji tag-pkg --nowait beaker-server-fedora-$REL-build rhpkg-simple-1.8-1.el7
