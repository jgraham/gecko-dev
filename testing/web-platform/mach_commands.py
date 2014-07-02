# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# Integrates the web-platform-tests test runner with mach.

from __future__ import unicode_literals, print_function

import mozpack.path
import os
import shutil
import sys

from StringIO import StringIO

from mozbuild.base import (
    MachCommandBase,
    MachCommandConditions as conditions,
    MozbuildObject,
)

from mach.decorators import (
    CommandArgument,
    CommandProvider,
    Command,
)

from wptrunner import wptcommandline

# This should probably be consolidated with similar classes in other test
# runners.
class InvalidTestPathError(Exception):
    """Exception raised when the test path is not valid."""

class WebPlatformTestsRunner(MozbuildObject):
    """Run web platform tests."""

    def __init__(self, topsrcdir, settings, log_manager, **kwargs):
        import machlogging
        from mozlog.structured import structuredlog

        log_manager = machlogging.StructuredLoggingManager()
        self._logger = structuredlog.StructuredLogger("web-platform-tests.mach")
        MozbuildObject.__init__(self, topsrcdir, settings, log_manager, **kwargs)

    def setup_kwargs(self, kwargs):
        build_path = os.path.join(self.topobjdir, 'build')
        if build_path not in sys.path:
            sys.path.append(build_path)

        if kwargs["binary"] is None:
            kwargs["binary"] = os.path.join(self.get_binary_path('app'))

        if kwargs["tests_root"] is None:
            kwargs["tests_root"] = os.path.join(self.topobjdir, '_tests', 'web-platform', "tests")

        if kwargs["metadata_root"] is None:
            kwargs["metadata_root"] = os.path.join(self.topobjdir, '_tests', 'web-platform', "meta")

        if kwargs["prefs_root"] is None:
            kwargs["prefs_root"] = os.path.join(self.topobjdir, '_tests', 'web-platform', "prefs")

        kwargs = wptcommandline.check_args(kwargs)

    def run_tests(self, **kwargs):
        from wptrunner import wptrunner

        self.setup_kwargs(kwargs)

        kwargs["capture_stdio"] = True
        logger = wptrunner.setup_logging(kwargs, {})
        self.log_manager.register_structured_logger(wptrunner.logger)
        self.log_manager.add_terminal_logging()

        result = wptrunner.run_tests(**kwargs)

        return int(not result)

    def list_test_groups(self, **kwargs):
        from wptrunner import wptrunner

        self.setup_kwargs(kwargs)

        result = wptrunner.list_test_groups(**kwargs)

class WebPlatformTestsUpdater(MozbuildObject):
    """Update web platform tests."""
    def __init__(self, topsrcdir, settings, log_manager, topobjdir=None):
        import machlogging
        from mozlog.structured import structuredlog

        log_manager = machlogging.StructuredLoggingManager()
        self._logger = structuredlog.StructuredLogger("web-platform-tests.update.mach")
        MozbuildObject.__init__(self, topsrcdir, settings, log_manager, topobjdir)

    def run_update(self, **kwargs):
        from wptrunner import update

        if kwargs["data_root"] is None:
            kwargs["data_root"] = os.path.join(self.topsrcdir, 'testing', 'web-platform')

        if kwargs["config"] is None:
            kwargs["config"] = os.path.join(self.topsrcdir, 'testing', 'web-platform', 'config.ini')

        update.run_update(**kwargs)

class WebPlatformTestsReduce(WebPlatformTestsRunner):

    def run_reduce(self, **kwargs):
        from wptrunner import wptrunner
        from wptrunner import reduce

        self.setup_kwargs(kwargs)

        kwargs["capture_stdio"] = True
        logger = reduce.setup_logging(kwargs, {})
        self.log_manager.register_structured_logger(reduce.logger)
        self.log_manager.add_terminal_logging()

        tests = reduce.do_reduce(**kwargs)

        if not tests:
            logger.warning("Test was not unstable")

        for item in tests:
            logger.info(item.id)

@CommandProvider
class MachCommands(MachCommandBase):
    @Command("web-platform-tests",
             category="testing",
             conditions=[conditions.is_firefox],
             parser=wptcommandline.create_parser(False))
    def run_web_platform_tests(self, **params):
        self.setup()
        wpt_runner = self._spawn(WebPlatformTestsRunner)

        if params["list_test_groups"]:
            return wpt_runner.list_test_groups(**params)
        else:
            return wpt_runner.run_tests(**params)

    @Command("web-platform-tests-update",
             category="testing",
             conditions=[conditions.is_firefox],
             parser=wptcommandline.create_parser_update(False))
    def update_web_platform_tests(self, **params):
        self.setup()
        self.virtualenv_manager.install_pip_package('html5lib==0.99')
        wpt_updater = self._spawn(WebPlatformTestsUpdater)
        return wpt_updater.run_update(**params)

    def setup(self):
        self._activate_virtualenv()
        self.virtualenv_manager.install_pip_package('py==1.4.14')

    @Command("web-platform-tests-reduce",
             category="testing",
             conditions=[conditions.is_firefox],
             parser=wptcommandline.create_parser_reduce(False))
    def unstable_web_platform_tests(self, **params):
        self.setup()
        wpt_reduce = self._spawn(WebPlatformTestsReduce)
        return wpt_reduce.run_reduce(**params)
