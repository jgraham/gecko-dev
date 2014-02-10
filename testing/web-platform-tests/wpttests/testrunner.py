# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from __future__ import unicode_literals

import urlparse
from Queue import Empty
import multiprocessing
from multiprocessing import Process, Pipe, current_process, Queue
import threading
import socket
import uuid
import traceback

import marionette
from mozlog.structured import structuredlog

from browser import FirefoxBrowser

# Special value used as a sentinal in various commands
Stop = object()

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


class TestRunner(object):
    def __init__(self, http_server_url, command_queue, result_queue,
                 marionette_port=None, binary=None):
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
        self.send_message("log", "debug", "Connecting to marionette on port %i" % self.marionette_port)
        self.browser = marionette.Marionette(host='localhost', port=self.marionette_port)
        #XXX Move this timeout somewhere
        success = self.browser.wait_for_port(20)
        if success:
            self.send_message("log", "debug", "Starting marionette session")
            self.browser.start_session()
            self.send_message("init_succeeded")
        else:
            self.send_message("log", "warning", "Failed to connect to marionette")
            self.send_message("init_failed")

        if success:
            try:
                self.browser.navigate(urlparse.urljoin(self.http_server_url, "/gecko_runner.html"))
                self.browser.execute_script("document.title = '%s'" % threading.current_thread().name)
            except:
                self.send_message("log", "warning", "Failed to connect to navigate initial page")
                self.send_message("init_failed")

    def teardown(self):
        print "teardown start"
        self.result_queue.cancel_join_thread()
        self.command_queue.cancel_join_thread()
        self.result_queue.close()
        self.result_queue = None
        self.command_queue.close()
        self.command_queue = None
        del self.browser
        self.browser = None
        print "teardown"
        #Close the marionette session

    def run(self):
        """Main loop accepting commands over the pipe and triggering
        the associated methods"""
        self.setup()
        commands = {"run_test": self.run_test,
                    "stop": self.stop}
        try:
            while True:
                command, args = self.command_queue.get()
                try:
                    rv = commands[command](*args)
                except Exception:
                    self.send_message("error",
                                      "Error running command %s with arguments %r:\n%s" %
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
            self.send_message("log", "error", "Lost marionette connection")
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
            # Clean up any unclosed windows
            # This doesn't account for the possibility the browser window
            # is totally hung. That seems less likely since we are still
            # getting data from marionette, but it might be just as well
            # to do a full restart in this case
            # XXX - this doesn't work at the moment because window_handles
            # only returns OS-level windows (see bug 907197)
            # while True:
            #     handles = self.browser.window_handles
            #     self.browser.switch_to_window(handles[-1])
            #     if len(handles) > 1:
            #         self.browser.close()
            #     else:
            #         break
            # Now need to check if the browser is still responsive and restart it if not
        except (socket.timeout, marionette.errors.InvalidResponseException) as e:
            # This can happen on a crash
            # Also, should check after the test if the firefox process is still running
            # and otherwise ignore any other result and set it to crash
            with result_lock:
                if not result_flag.is_set():
                    result_flag.set()
                    result = (test.result_cls("CRASH", None), [])
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


def start_runner(runner_cls, http_server_url, marionette_port, browser_binary, runner_command_queue, runner_result_queue,
                 stop_flag):
    try:
        runner = runner_cls(http_server_url, runner_command_queue, runner_result_queue, marionette_port=marionette_port,
                            binary=browser_binary)
        runner.run()
    except KeyboardInterrupt:
        stop_flag.set()
    finally:
        runner_command_queue = None
        runner_result_queue = None


class TestRunnerManager(threading.Thread):
    init_lock = threading.Lock()

    def __init__(self, suite_name, server_url, browser_binary, run_info, tests_queue,
                 stop_flag, runner_cls, marionette_port=None, browser_cls=FirefoxBrowser):
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
        self.suite_name = suite_name
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
        self.logger = structuredlog.StructuredLogger(self.suite_name)
        self.browser = self.browser_cls(self.browser_binary, self.logger, self.marionette_port)
        try:
            self.init()
            while True:
                commands = {"init_succeeded": self.init_succeeded,
                            "init_failed": self.init_failed,
                            "test_ended": self.test_ended,
                            "restart_test": self.restart_test,
                            "log": self.log}
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
            self.logger.debug("init_failed called from timer")
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
                    self.logger.warning("Forcibly terminating runner process")
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
            self.logger.debug("No more tests")
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
            if test.disabled(result.name):
                continue
            expected = test.expected(result.name)
            is_unexpected = expected != result.status

            if is_unexpected:
                self.unexpected_count += 1
                self.logger.debug("Unexpected count in this thread %i" % self.unexpected_count)
            self.logger.test_status(test.id,
                                    result.name,
                                    result.status,
                                    message=result.message,
                                    expected=expected)

        # Check if we crashed after getting a result
#        if not self.browser.is_alive():
#            logger.debug("Changing status of test %r to crash" % (test.id,))
#            file_result.status = "CRASH"

        #Write the result of the test harness
        expected = test.expected()
        status = file_result.status if file_result.status != "EXTERNAL-TIMEOUT" else "TIMEOUT"
        is_unexpected = expected != status
        if is_unexpected:
            self.unexpected_count += 1
            self.logger.debug("Unexpected count in this thread %i" % self.unexpected_count)
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
        self.logger.info("Restarting runner")
        self.stop_runner()
        self.init()

    def log(self, level, message):
        getattr(self.logger, level)(message)


class ManagerGroup(object):
    def __init__(self, suite_name, runner_cls, run_info, size, server_url, binary_path,
                 browser_cls=FirefoxBrowser):
        """Main thread object that owns all the TestManager threads."""
        self.suite_name = suite_name
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
        self.logger = structuredlog.StructuredLogger(suite_name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self, tests_queue):
        """Start all managers in the group"""
        used_ports = set()
        self.logger.debug("Using %i processes" % self.size)
        for i in range(self.size):
            marionette_port = get_free_port(2828, exclude=used_ports)
            used_ports.add(marionette_port)
            manager = TestRunnerManager(self.suite_name,
                                        self.server_url,
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
        self.logger.debug("Stop flag set")
        self.wait()

    def unexpected_count(self):
        count = 0
        for item in self.pool:
            count += item.unexpected_count
        return count
