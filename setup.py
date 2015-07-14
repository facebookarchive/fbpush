# Copyright (c) 2015-present, Facebook, Inc.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree. An additional grant
# of patent rights can be found in the PATENTS file in the same directory.

from setuptools import setup
import sys

if sys.version_info < (2, 7):
    # It is python 2.6
    test_module = 'tests'
else:
    test_module = 'nose.collector'


setup(
    name='FBPUSH',
    version='0.1.0alpha',
    packages=['fbpush'],
    include_package_data=True,
    package_data={'fbpush': ['reminder_msg']},
    data_files=[('/var/lib/fbpush/config', ['fbpush/config/devices.json']),
                ('/var/lib/fbpush/config',
                 ['fbpush/config/configuration.json']),
                ],
    entry_points={
        'console_scripts': ['fbpush=fbpush.main:run_main', ],
    },
    license='BSD+',
    description='CLI tool to push config files to juniper network devices',

    long_description=open("README.md").read(),
    install_requires=['Twisted', 'pyOpenSSL == 0.12', 'service_identity',
                      'mock', 'nose>=1.0', ],
    test_suite=test_module,
)
