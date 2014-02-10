# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import os

import mozprocess
from mozprofile.profile import FirefoxProfile
from mozprofile.permissions import ServerLocations
from mozrunner import FirefoxRunner

here = os.path.split(__file__)[0]

class ProcessHandler(mozprocess.ProcessHandlerMixin):
    pass

class Browser(object):
    process_cls = None

    def __init__(self, binary, logger, marionette_port):
        self.binary = binary
        self.logger = logger
        self.marionette_port = marionette_port
        self.runner = None

    def start(self):
        raise NotImplementedError

    def stop():
        raise NotImplementedError

    def on_output(self, line):
        raise NotImplementedError

    def is_alive(self):
        raise NotImplementedError

class NullBrowser(Browser):
    """No-op browser to use in scenarios where the TestManager shouldn't
    actually own the browser process (e.g. servo where we start one browser
    per test)"""
    def start(self):
        pass

    def stop(self):
        pass

    def is_alive(self):
        return True

class FirefoxBrowser(Browser):
    def start(self):
        env = os.environ.copy()
        env['MOZ_CRASHREPORTER_NO_REPORT'] = '1'

        locations = ServerLocations(filename=os.path.join(here, "server-locations.txt"))
        profile = FirefoxProfile(locations=locations, proxy=True)
        profile.set_preferences({"marionette.defaultPrefs.enabled": True,
                                 "marionette.defaultPrefs.port": self.marionette_port,
                                 "dom.disable_open_during_load": False,
                                 "dom.max_script_run_time": 0,
                                 "browser.shell.checkDefaultBrowser": False,
                                 "browser.dom.window.dump.enabled": True})

        self.runner = FirefoxRunner(profile,
                                    self.binary,
                                    cmdargs=["--marionette", "about:blank"],
                                    env=env,
                                    kp_kwargs={"processOutputLine": [self.on_output]},
                                    process_class=ProcessHandler)

        self.logger.debug("Starting Firefox")
        self.runner.start()
        self.logger.debug("Firefox Started")

    def stop(self):
        self.logger.debug("Stopping browser")
        if self.runner is not None:
            self.runner.stop()

    def pid(self):
        if self.runner.process_handler is not None:
            try:
                pid = self.runner.process_handler.pid
            except AttributeError:
                pid = None
        else:
            pid = None

    def on_output(self, line):
        """Write a line of output from the firefox process to the log"""
        self.logger.process_output(self.pid(),
                                   line.decode("utf8"),
                                   command=" ".join(self.runner.command))

    def is_alive(self):
        return self.runner.is_running()
