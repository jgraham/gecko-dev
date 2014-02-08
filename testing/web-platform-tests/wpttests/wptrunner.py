# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from __future__ import unicode_literals

import sys
import os
import urlparse
import json
from Queue import Empty
import multiprocessing
from multiprocessing import Process, Pipe, current_process, Queue
import threading
import socket
import hashlib
from collections import defaultdict
import uuid
import logging
import traceback

import marionette
import mozprocess
import mozprofile
from mozprofile.profile import FirefoxProfile
from mozprofile.permissions import ServerLocations
from mozrunner import FirefoxRunner

import structuredlog
import metadata
import wpttest
import wptcommandline


here = os.path.split(__file__)[0]

#TODO
# reftest details (window size+ lots more)
# logging
# Documentation
# better status report
# correct output format
# webdriver tests
# HTTP server crashes
# Expected test results

"""Runner for web-platform-tests

The runner has several design goals:

* Tests should run with no modification from upstream.

* Tests should be regarded as "untrusted" so that errors, timeouts and even
  crashes in the tests can be handled without failing the entire test run.

* For performance tests can be run in multiple browsers in parallel.

The upstream repository has the facility for creating a test manifest in JSON
format. This manifest is used directly to determine which tests exist. Local
metadata files are used to store the expected test results.

"""

logger = structuredlog.getOutputLogger("WPT")


def setup_stdlib_logger():
    logging.root.handlers = []
    adapter_cls = structuredlog.get_adapter_cls()
    logging.root = adapter_cls(logging.root)


def do_test_relative_imports(test_root):
    global serve

    sys.path.insert(0, os.path.join(test_root))
    sys.path.insert(0, os.path.join(test_root, "tools", "scripts"))
    import serve


def make_wrapper(cmd, cmd_args):
    class WrappedCommand(type):
        def __call__(cls, *args, **kwargs):
            all_args = ([cmd] + cmd_args + args[0],) + args[1:]
            return super(WrappedCommand, cls).__call__(*all_args, **kwargs)

    def inner(cls):
        class Command(cls):
            __metaclass__ = WrappedCommand
        return Command

    return inner


XvfbWrapped = make_wrapper("xvfb-run",
                           ["-a", "--server-args=+extension RANDR -screen 0 800x600x24"])


class TestEnvironment(object):
    def __init__(self, test_path):
        """Context manager that owns the test environment i.e. the http and
        websockets servers"""
        self.test_path = test_path
        self.server = None
        self.config = None

    def __enter__(self):
        config = serve.load_config(os.path.join(self.test_path, "config.default.json"),
                                   os.path.join(here, "config.json"))
        serve.logger = serve.default_logger("info")
        self.config, self.servers = serve.start(config)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for scheme, servers in self.servers.iteritems():
            for port, server in servers:
                server.kill()


def get_free_port(start_port, exclude=None):
    port = start_port
    while True:
        if exclude and port in exclude:
            port += 1
            continue
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
        except socket.error:
            port += 1
        else:
            return port
        finally:
            s.close()


#Special value used as a sentinal in various commands
Stop = object()


class TestRunner(object):
    def __init__(self, http_server_url, command_queue, result_queue, marionette_port=None, binary=None):
        """Base class for actually running tests.

        Each test type will have a derived class overriding the do_test
        and convert_result methods.

        Each instance of this class is expected to run in its own process
        and each operates its own event loop with two basic commands, one
        to run a test and one to shut down the instance.

        :param http_server_url: url to the main http server over which tests
                                will be loaded
        :param command_queue: subprocess.Queue used to send commands to the
                              process
        :param result_queue: subprocess.Queue used to send results to the
                             parent TestManager process
        :param marionette_port: port number to use for marionette, or None
                                to use a free port
        """
        self.http_server_url = http_server_url

        self.command_queue = command_queue
        self.result_queue = result_queue

        if marionette_port is None:
            marionette_port = get_free_port(2828)
        self.marionette_port = marionette_port
        self.binary = binary
        self.timer = None
        self.window_id = str(uuid.uuid4())
        self.timeout_multiplier = 1  # TODO: Adjust this depending on tests and hardware

    def setup(self):
        """Connect to browser via marionette"""
        logger.debug("Connecting to marionette on port %i" % self.marionette_port)
        self.browser = marionette.Marionette(host='localhost', port=self.marionette_port)
        #XXX Move this timeout somewhere
        success = self.browser.wait_for_port(20)
        if success:
            logger.debug("Marionette port aquired")
            self.browser.start_session()
            logger.debug("Marionette session started")
            self.send_message("init_succeeded")
        else:
            logger.warning("Failed to connect to marionette")
            self.send_message("init_failed")

        if success:
            try:
                self.browser.navigate(urlparse.urljoin(self.http_server_url, "/gecko_runner.html"))
                self.browser.execute_script("document.title = '%s'" % threading.current_thread().name)
            except:
                logger.warning("Failed to connect to navigate initial page")
                self.send_message("init_failed")

    def teardown(self):
        logger.debug("TestRunner teardown")
        self.result_queue.cancel_join_thread()
        self.command_queue.cancel_join_thread()
        self.result_queue.close()
        self.result_queue = None
        self.command_queue.close()
        self.command_queue = None
        del self.browser
        self.browser = None
        #Close the marionette session

    def run(self):
        """Main loop accepting commands over the pipe and triggering
        the associated methods"""
        logger.debug("Run TestRunner")
        self.setup()
        commands = {"run_test": self.run_test,
                    "stop": self.stop}
        try:
            while True:
                command, args = self.command_queue.get()
                try:
                    rv = commands[command](*args)
                except Exception:
                    logger.error("Error running command %s with arguments %r:\n%s" %
                                 (command, args, traceback.format_exc()))
                else:
                    if rv is Stop:
                        break
        finally:
            self.teardown()

    def connection_alive(self):
        try:
            #Get a simple property over the connection
            self.browser.current_window_handle
        except (socket.timeout, marionette.errors.InvalidResponseException):
            return False
        return True

    def run_test(self, test):
        """Run a single test.

        This method is independent of the test type, and calls
        do_test to implement the type-sepcific testing functionality.
        """
        if not self.connection_alive():
            logger.error("Lost marionette connection")
            self.send_message("restart_test", test)
            return Stop

        #Lock to prevent races between timeouts and other results
        #This might not be strictly necessary if we need to deal
        #with the result changing post-hoc anyway (e.g. due to detecting
        #a crash after we get the data back from marionette)
        result = None
        result_flag = threading.Event()
        result_lock = threading.Lock()

        def timeout_func():
            with result_lock:
                if not result_flag.is_set():
                    result_flag.set()
                    result = (test.result_cls("EXTERNAL-TIMEOUT", None), [])
                    self.send_message("test_ended", test, result)

        self.timer = threading.Timer(test.timeout + 10, timeout_func)
        self.timer.start()

        self.browser.set_script_timeout((test.timeout + 5) * 1000)

        try:
            result = self.convert_result(test, self.do_test(test))
        except marionette.errors.ScriptTimeoutException:
            with result_lock:
                if not result_flag.is_set():
                    result_flag.set()
                    result = (test.result_cls("EXTERNAL-TIMEOUT", None), [])
            #Clean up any unclosed windows
            #This doesn't account for the possibility the browser window
            #is totally hung. That seems less likely since we are still
            #getting data from marionette, but it might be just as well
            #to do a full restart in this case
            #XXX - this doesn't work at the moment because window_handles
            #only returns OS-level windows (see bug 907197)
            # while True:
            #     handles = self.browser.window_handles
            #     self.browser.switch_to_window(handles[-1])
            #     if len(handles) > 1:
            #         self.browser.close()
            #     else:
            #         break
            #Now need to check if the browser is still responsive and restart it if not
        except (socket.timeout, marionette.errors.InvalidResponseException) as e:
            #This can happen on a crash
            #XXX Maybe better to have a specific crash message?
            #Also, should check after the test if the firefox process is still running
            #and otherwise ignore any other result and set it to crash
            with result_lock:
                if not result_flag.is_set():
                    result_flag.set()
                    result = (test.result_cls("CRASH", None), [])
            logger.warning(unicode(e))
        finally:
            self.timer.cancel()

        with result_lock:
            if result:
                self.send_message("test_ended", test, result)

    def do_test(self, test):
        raise NotImplementedError

    def convert_result(self, test, result):
        raise NotImplementedError

    def stop(self):
        return Stop

    def send_message(self, command, *args):
        self.result_queue.put((command, args))


class TestharnessTestRunner(TestRunner):
    harness_codes = {0: "OK",
                     1: "ERROR",
                     2: "TIMEOUT"}

    test_codes = {0: "PASS",
                  1: "FAIL",
                  2: "TIMEOUT",
                  3: "NOTRUN"}

    def __init__(self, *args, **kwargs):
        TestRunner.__init__(self, *args, **kwargs)
        self.script = open(os.path.join(here, "testharness.js")).read()

    def do_test(self, test):
        assert len(self.browser.window_handles) == 1
        return self.browser.execute_async_script(
            self.script % {"abs_url": urlparse.urljoin(self.http_server_url, test.url),
                           "url": test.url,
                           "window_id": self.window_id,
                           "timeout_multiplier": self.timeout_multiplier,
                           "timeout": test.timeout * 1000}, new_sandbox=False)

    def convert_result(self, test, result):
        """Convert a JSON result into a (TestResult, [SubtestResult]) tuple"""
        assert result["test"] == test.url, ("Got results from %s, expected %s" %
                                            (result["test"], test.url))
        harness_result = test.result_cls(self.harness_codes[result["status"]], result["message"])
        return (harness_result,
                [test.subtest_result_cls(subtest["name"], self.test_codes[subtest["status"]],
                                         subtest["message"]) for subtest in result["tests"]])


class ReftestTestRunner(TestRunner):
    def __init__(self, *args, **kwargs):
        TestRunner.__init__(self, *args, **kwargs)
        with open(os.path.join(here, "reftest.js")) as f:
            self.script = f.read()
        self.ref_hashes = {}
        self.ref_urls_by_hash = defaultdict(set)

    def do_test(self, test):
        url, ref_type, ref_url = test.url, test.ref_type, test.ref_url
        hashes = {"test": None,
                  "ref": self.ref_hashes.get(ref_url)}
        self.browser.execute_script(self.script)
        self.browser.switch_to_window(self.browser.window_handles[-1])
        for url_type, url in [("test", url), ("ref", ref_url)]:
            if hashes[url_type] is None:
                #Would like to do this in a new tab each time, but that isn't
                #easy with the current state of marionette
                self.browser.navigate(urlparse.urljoin(self.http_server_url, url))
                screenshot = self.browser.screenshot()
                #strip off the data:img/png, part of the url
                if screenshot.startswith("data:image/png;base64,"):
                    screenshot = screenshot.split(",", 1)[1]
                hashes[url_type] = hashlib.sha1(screenshot).hexdigest()

        self.ref_urls_by_hash[hashes["ref"]].add(ref_url)
        self.ref_hashes[ref_url] = hashes["ref"]

        if ref_type == "==":
            passed = hashes["test"] == hashes["ref"]
        elif ref_type == "!=":
            passed = hashes["test"] != hashes["ref"]
        else:
            raise ValueError

        return "PASS" if passed else "FAIL"

    def teardown(self):
        count = 0
        for hash_val, urls in self.ref_urls_by_hash.iteritems():
            if len(urls) > 1:
                logger.info("The following %i reference urls appear to be equivalent:\n %s" %
                            (len(urls), "\n  ".join(urls)))
                count += len(urls) - 1
        TestRunner.teardown(self)

    def convert_result(self, test, result):
        """Reftests only have a single result, so collapse everything down into the harness result."""
        return (test.result_cls(result, None), [])


class ServoTestRunner(TestharnessTestRunner):
    def __init__(self, *args, **kwargs):
        TestharnessTestRunner.__init__(self, *args, **kwargs)
        self.result_data = None
        self.result_flag = None

    def setup(self):
        self.send_message("init_succeeded")
        return True

    def run_test(self, test):
        self.result_data = None
        self.result_flag = threading.Event()
        proc = ProcessHandler(self.binary, [urlparse.urljoin(self.http_server_url, test.url)],
                              processOutputLine=[self.on_output])
        proc.run()
        #Now wait to get the output we expect, or until we reach the timeout
        self.result_flag.wait(test.timeout + 5)

        if self.result_flag.is_set():
            assert self.result_data is not None
            result = self.convert_result(test, self.result_data)
            proc.kill()
        else:
            if proc.pid is None:
                result = (test.result_cls("CRASH", None), [])
            else:
                proc.kill()
                result = (test.result_cls("TIMEOUT", None), [])
        self.send_message("test_ended", test, result)

    def on_output(self, line):
        prefix = "ALERT: RESULT: "
        line = line.decode("utf8")
        if line.startswith(prefix):
            self.result_data = json.loads(line[len(prefix):])
            self.result_flag.set()

    def convert_result(self, test, result):
        result["test"] = test.url
        return TestharnessTestRunner.convert_result(self, test, result)

def start_runner(runner_cls, http_server_url, marionette_port, browser_binary, runner_command_queue, runner_result_queue,
                 stop_flag):
    try:
        runner = runner_cls(http_server_url, runner_command_queue, runner_result_queue, marionette_port=marionette_port,
                            binary=browser_binary)
        runner.run()
    except KeyboardInterrupt:
        stop_flag.set()
        logger.debug("Runner process got signal")
    finally:
        runner_command_queue = None
        runner_result_queue = None


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


class TestRunnerManager(threading.Thread):
    init_lock = threading.Lock()

    def __init__(self, server_url, browser_binary, run_info, tests_queue,
                 stop_flag, runner_cls=TestharnessTestRunner,
                 marionette_port=None, browser_cls=FirefoxBrowser):
        """Thread that owns a single TestRunner process and any processes required
        by the TestRunner (e.g. the Firefox binary).

        TestRunnerManagers are in control of the overall testing process. Over
        the lifetime of a testrun, the TestRunnerManager will:

        * Start the binary of the program under test
        * Start the TestRunner
        * Cause tests to be run:
          - Pull a test off the test queue
          - Forward the test to the TestRunner
          - Collect the test results and output them
          - Take any remedial action required e.g. restart crashed or hung
            processes
        """
        self.http_server_url = server_url
        self.browser_binary = browser_binary
        self.tests_queue = tests_queue
        self.run_info = run_info

        # Flags used to shut down this thread if we get a sigint
        self.parent_stop_flag = stop_flag
        self.child_stop_flag = multiprocessing.Event()

        self.command_queue = None
        self.remote_queue = None

        self.browser = None
        self.test_runner_proc = None
        self.runner_cls = runner_cls
        self.marionette_port = marionette_port
        self.browser_cls = browser_cls
        threading.Thread.__init__(self)
        # This is started in the actual new thread
        self.logger = None

        # The test that is currently running
        self.test = None

        self.unexpected_count = 0

        # This may not really be what we want
        self.daemon = True

        self.init_fail_count = 0
        self.max_init_fails = 5
        self.init_timer = None

    def run(self):
        """Main loop for the TestManager.

        TestManagers generally recieve commands from their
        TestRunner updating them on the status of a test. They
        may also have a stop flag set by the main thread indicating
        that the manager should shut down the next time the event loop
        spins."""
        self.logger = structuredlog.getOutputLogger("WPT")
        self.browser = self.browser_cls(self.browser_binary, self.logger, self.marionette_port)
        try:
            self.init()
            while True:
                commands = {"init_succeeded": self.init_succeeded,
                            "init_failed": self.init_failed,
                            "test_ended": self.test_ended,
                            "restart_test": self.restart_test}
                try:
                    command, data = self.command_queue.get(True, 1)
                except IOError:
                    if not self.should_stop():
                        self.logger.error("Got IOError from poll")
                        self.restart_runner()
                except Empty:
                    command = None

                if self.should_stop():
                    break

                if command is not None:
                    if commands[command](*data) is Stop:
                        break
                else:
                    if not self.test_runner_proc.is_alive():
                        self.logger.info("Test runner process shut down")
                        if self.tests_queue.empty() and self.test is None:
                            # This happens when we run out of tests;
                            # We ask the runner to stop, it shuts itself
                            # down and then we end up here
                            # An alternate implementation strategy would be to have the
                            # runner signal that it is done just before it terminates
                            self.browser.stop()
                            break
                        elif self.test is not None:
                            # This could happen if the test runner crashed for some other
                            # reason
                            self.logger.info("Last test did not complete, restarting")
                            self.restart_test(self.test)
                        else:
                            self.logger.info("More tests found, but runner process died, restarting")
                            self.restart_runner()
        finally:
            self.stop_runner()
            self.logger.debug("TestRunnerManager main loop terminating")

    def should_stop(self):
        return self.child_stop_flag.is_set() or self.parent_stop_flag.is_set()

    def init(self):
        """Create the Firefox process that is being tested,
        and the TestRunner process that will run the tests."""
        #It seems that this lock is helpful to prevent some race that otherwise
        #sometimes stops the spawned processes initalising correctly, and
        #leaves this thread hung
        self.logger.debug("Init called, starting browser and runner")

        def init_failed():
            #This is called from a seperate thread, so we send a message to the
            #main loop so we get back onto the manager thread
            logger.debug("init_failed called from timer")
            if self.command_queue:
                self.command_queue.put(("init_failed", ()))
            else:
                self.child_stop_flag.set()


        with self.init_lock:
            #To guard against cases where we fail to connect with marionette for
            #whatever reason
            #TODO: make this timeout configurable
            self.init_timer = threading.Timer(30, init_failed)
            self.init_timer.start()

            assert self.command_queue is None
            assert self.remote_queue is None

            self.command_queue = Queue()
            self.remote_queue = Queue()

            self.browser.start()
            self.start_test_runner()

    def init_succeeded(self):
        """Callback when we have started the browser, connected via
        marionette, and we are ready to start testing"""
        self.logger.debug("Init succeeded")
        self.init_timer.cancel()
        self.init_fail_count = 0
        if self.test is not None:
            self.start_test(self.test)
        else:
            self.start_next_test()

    def init_failed(self):
        """Callback when we can't connect to the browser via
        marionette for some reason"""
        self.init_fail_count += 1
        self.logger.error("Init failed %i" % self.init_fail_count)
        self.init_timer.cancel()
        if self.init_fail_count < self.max_init_fails:
            self.restart_runner()
        else:
            self.logger.warning("Test runner failed to initalise correctly; shutting down")
            return Stop

    def start_test_runner(self):
        assert self.command_queue is not None
        assert self.remote_queue is not None
        self.test_runner_proc = Process(target=start_runner,
                                        args=(self.runner_cls,
                                              self.http_server_url,
                                              self.marionette_port,
                                              self.browser_binary,
                                              self.remote_queue,
                                              self.command_queue,
                                              self.child_stop_flag))
        self.test_runner_proc.start()
        self.logger.debug("Test runner started")

    def send_message(self, command, *args):
        self.remote_queue.put((command, args))

    def cleanup(self):
        self.logger.debug("TestManager cleanup")
        self.test_runner_proc = None
        if self.command_queue:
            self.command_queue.close()
            self.command_queue = None
        if self.remote_queue:
            self.remote_queue.close()
            self.remote_queue = None

    def stop_runner(self):
        """Stop the TestRunner and the Firefox binary."""
        self.logger.debug("Stopping runner")

        try:
            self.browser.stop()
            if self.test_runner_proc:
                self.send_message("stop")
                self.test_runner_proc.join(10)
                if self.test_runner_proc.is_alive():
                    #This might leak a file handle from the queue
                    logger.warning("Forcibly terminating runner process")
                    self.test_runner_proc.terminate()
        finally:
            self.cleanup()

    def start_next_test(self):
        """Start the next test in the queue, or stop the
        TestRunner if there are no more tests."""
        assert self.test is None
        try:
            test = self.tests_queue.get(False)
        except Empty:
            logger.debug("No more tests")
            self.send_message("stop")
        else:
            self.logger.test_start(test.id)
            self.start_test(test)

    def start_test(self, test):
        """Send the message to actually start a test"""
        self.test = test
        self.logger.debug("Starting test %r" % (test.id,))
        assert self.test_runner_proc.is_alive()
        assert self.browser.is_alive()
        self.send_message("run_test", self.test)

    def restart_test(self, test):
        """Restart the runner and restart the current test.
        This can happen if the browser crashed after completing
        the previous test"""
        assert test == self.test
        self.restart_runner()

    def test_ended(self, test, results):
        """Handle the end of a test.

        Output the result of each subtest, and the result of the overall
        harness to the logs.
        """
        assert test == self.test
        #Write the result of each subtest
        file_result, test_results = results
        for result in test_results:
            if test.disabled(self.run_info, result.name):
                continue
            expected = test.expected_status(self.run_info, result.name)
            is_unexpected = expected != result.status

            if is_unexpected:
                self.unexpected_count += 1
                logger.debug("Unexpected count in this thread %i" % self.unexpected_count)
            self.logger.test_status(test.id,
                                    result.name,
                                    result.status,
                                    message=result.message,
                                    expected=expected)

        #Check if we crashed after getting a result
#        if not self.browser.is_alive():
#            logger.debug("Changing status of test %r to crash" % (test.id,))
#            file_result.status = "CRASH"

        #Write the result of the test harness
        expected = test.expected_status(self.run_info)
        status = file_result.status if file_result.status != "EXTERNAL-TIMEOUT" else "TIMEOUT"
        is_unexpected = expected != status
        if is_unexpected:
            self.unexpected_count += 1
            logger.debug("Unexpected count in this thread %i" % self.unexpected_count)
        self.logger.test_end(test.id,
                             status,
                             message=file_result.message,
                             expected=expected)

        self.test = None

        #Handle starting the next test, with a runner restart if required
        if file_result.status in ("CRASH", "EXTERNAL-TIMEOUT"):
            self.restart_runner()
        else:
            self.start_next_test()

    def restart_runner(self):
        """Stop and restart the TestRunner"""
        logger.info("Restarting runner")
        self.stop_runner()
        self.init()


class ManagerGroup(object):
    def __init__(self, runner_cls, run_info, size, server_url, binary_path,
                 browser_cls=FirefoxBrowser):
        """Main thread object that owns all the TestManager threads."""
        self.server_url = server_url
        self.binary_path = binary_path
        self.size = size
        self.runner_cls = runner_cls
        self.browser_cls = browser_cls
        self.pool = set()
        #Event that is polled by threads so that they can gracefully exit in the face
        #of sigint
        self.stop_flag = threading.Event()
        self.run_info = run_info

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self, tests_queue):
        """Start all managers in the group"""
        used_ports = set()
        logger.debug("Using %i processes" % self.size)
        for i in range(self.size):
            marionette_port = get_free_port(2828, exclude=used_ports)
            used_ports.add(marionette_port)
            manager = TestRunnerManager(self.server_url,
                                        self.binary_path,
                                        self.run_info,
                                        tests_queue,
                                        self.stop_flag,
                                        runner_cls=self.runner_cls,
                                        marionette_port=marionette_port,
                                        browser_cls=self.browser_cls)
            manager.start()
            self.pool.add(manager)

    def is_alive(self):
        """Boolean indicating whether any manager in the group is still alive"""
        for manager in self.pool:
            if manager.is_alive():
                return True
        return False

    def wait(self):
        """Wait for all the managers in the group to finish"""
        for item in self.pool:
            item.join()

    def stop(self):
        """Set the stop flag so that all managers in the group stop as soon
        as possible"""
        self.stop_flag.set()
        logger.debug("Stop flag set")
        self.wait()

    def unexpected_count(self):
        count = 0
        for item in self.pool:
            count += item.unexpected_count
        return count


def queue_tests(test_root, metadata_root, test_types, run_info, include_filters):
    """Read in the tests from the manifest file and add them to a queue"""
    test_ids = []
    tests_by_type = defaultdict(Queue)

    tests_metadata = metadata.TestsMetadata(test_root, metadata_root)

    for test_type in test_types:
        for test in tests_metadata.manifest.itertype(test_type):
            queue_test = False
            if include_filters:
                for filter_str in include_filters:
                    if test.url.startswith(filter_str):
                        queue_test = True
            else:
                queue_test = True
            if queue_test:
                test = wpttest.from_manifest(test, tests_metadata)
                if not test.disabled(run_info):
                    tests_by_type[test_type].put(test)
                    test_ids.append(test.id)

    return test_ids, tests_by_type


class LoggingWrapper(object):
    """Wrapper for file like objects to redirect output to logger
    instead"""
    def __init__(self, logger, level="info", prefix=None):
        self.logger = logger
        self.log_func = getattr(self.logger, level)
        self.prefix = prefix

    def write(self, data):
        if data.endswith("\n"):
            data = data[:-1]
        if data.endswith("\r"):
            data = data[:-1]
        if self.prefix is not None:
            data = "%s: %s" % (self.prefix, data)
        self.log_func(data)

    def flush(self):
        pass

def setup_logging(output_file, log_stdout):
    setup_stdlib_logger()

    if output_file is not None:
        logger.add_handler(structuredlog.StreamHandler(output_file))

    if log_stdout:
        logger.add_handler(structuredlog.StreamHandler(sys.stdout))


browser_classes = {"firefox": FirefoxBrowser,
                   "servo": NullBrowser}

test_runner_classes = {"firefox": {"reftest": ReftestTestRunner,
                                   "testharness": TestharnessTestRunner},
                       "servo": {"testharness": ServoTestRunner}}

def run_tests(binary, tests_root, metadata_root, test_types,
              processes=1, xvfb=False, include=None, output_file=None,
              log_stdout=False, capture_stdio=True, product="firefox"):
    setup_logging(output_file, log_stdout)

    original_stdio = (sys.stdout, sys.stderr)
    if capture_stdio:
        sys.stdout = LoggingWrapper(logger, prefix="STDOUT")
        sys.stderr = LoggingWrapper(logger, level="info", prefix="STDERR")

    try:
        do_test_relative_imports(tests_root)

        run_info = wpttest.RunInfo(False)

        logger.info("Using %i client processes" % processes)

        browser_cls = browser_classes[product]

        unexpected_count = 0

        with TestEnvironment(tests_root) as test_environment:
            base_server = "http://%s:%i" % (test_environment.config["host"],
                                            test_environment.config["ports"]["http"][0])
            test_ids, test_queues = queue_tests(tests_root, metadata_root,
                                                test_types, run_info, include)
            logger.suite_start(test_ids)
            for test_type in test_types:
                tests_queue = test_queues[test_type]
                runner_cls = test_runner_classes[product].get(test_type)

                if runner_cls is None:
                    logger.error("Unsupported test type %s for product %s" % (test_type, product))
                    continue

                if xvfb:
                    #XXX this is broken
                    browser_cls = XvfbWrapped(browser_cls)

                with ManagerGroup(runner_cls,
                                  run_info,
                                  processes,
                                  base_server,
                                  binary,
                                  browser_cls=browser_cls) as manager_group:
                    try:
                        manager_group.start(tests_queue)
                    except KeyboardInterrupt:
                        logger.debug("Main thread got signal")
                        manager_group.stop()
                    manager_group.wait()
                unexpected_count += manager_group.unexpected_count()

            logger.suite_end()
    finally:
        sys.stdout, sys.stderr = original_stdio

    logger.info("Got %i unexpected results" % unexpected_count)

    if capture_stdio:
        sys.stdout, sys.stderr = original_stdio

    return manager_group.unexpected_count() == 0


def main():
    """Main entry point when calling from the command line"""
    args = wptcommandline.parse_args()
    kwargs = vars(args)

    if args.output_file:
        output_dir = os.path.split(kwargs["output_file"])[0]
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        kwargs["output_file"] = open(kwargs["output_file"], "w")

    return run_tests(**kwargs)
