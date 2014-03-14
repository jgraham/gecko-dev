# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from setuptools import setup, find_packages

PACKAGE_NAME = 'wptrunner'
PACKAGE_VERSION = '0.1'

# dependencies
with open('requirements.txt') as f:
    deps = f.read().splitlines()

setup(name=PACKAGE_NAME,
      version=PACKAGE_VERSION,
      description="Harness for running the W3C web-platform-tests against various Mozilla products",
      author='Mozilla Automation and Testing Team',
      author_email='tools@lists.mozilla.org',
      license='MPL 1.1/GPL 2.0/LGPL 2.1',
      packages=find_packages(exclude=["tests", "metadata"]),
      scripts=["runtests.py", "update.py"],
      zip_safe=False,
      platforms =['Any'],
      classifiers=['Development Status :: 4 - Beta',
                   'Environment :: Console',
                   'Intended Audience :: Developers',
                   'License :: OSI Approved :: Mozilla Public License 1.1 (MPL 1.1)',
                   'Operating System :: OS Independent',
                  ],
      install_requires=deps
     )
