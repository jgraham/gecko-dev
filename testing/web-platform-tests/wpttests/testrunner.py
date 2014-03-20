# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from __future__ import unicode_literals

import sys

import urlparse
from Queue import Empty
import multiprocessing
from multiprocessing import Process, current_process, Queue
import threading
import uuid
import socket
import traceback

from mozlog.structured import structuredlog

# Special value used as a sentinal in various commands
Stop = object()

class TestRunner(object):
    def __init__(self, command_queue, result_queue, executor):
        """Class implementing the main loop for running tests.

        This class delegates the job of actually running a test to the executor
        that is passed in.

        :param command_queue: subprocess.Queue used to send commands to the
                              process
        :param result_queue: subprocess.Queue used to send results to the
                             parent TestManager process
        :param executor: TestExecutor object that will actually run a test.
        """
        self.command_queue = command_queue
        self.result_queue = result_queue

        self.executor = executor
        self.name = current_process().name

    def __enter__(self):
        return self

    def __exit__(self, *args, **kwargs):
        self.teardown()

    def setup(self):
        self.executor.setup(self)

    def teardown(self):
        print self.name + " TestRunner teardown"
        self.executor.teardown()
        self.result_queue = None
        self.command_queue = None
        self.browser = None

    def run(self):
        """Main loop accepting commands over the pipe and triggering
        the associated methods"""
        self.setup()
        commands = {"run_test": self.run_test,
                    "stop": self.stop}
        while True:
            command, args = self.command_queue.get()
            print self.name + " " + command
            try:
                rv = commands[command](*args)
            except Exception:
                self.send_message("error",
                                  "Error running command %s with arguments %r:\n%s" %
                                  (command, args, traceback.format_exc()))
            else:
                if rv is Stop:
                    break
        print self.name + " Exiting"

    def stop(self):
        return Stop

    def run_test(self, test):
        return self.executor.run_test(test)

    def send_message(self, command, *args):
        self.result_queue.put((command, args))


def start_runner(runner_command_queue, runner_result_queue, browser,
                 executor_cls, executor_kwargs, stop_flag):
    try:
        executor = executor_cls(browser, **executor_kwargs)
        with TestRunner(runner_command_queue, runner_result_queue, executor) as runner:
            try:
                runner.run()
            except KeyboardInterrupt:
                stop_flag.set()
            except Exception as e:
                print e
    finally:
        runner_command_queue = None
        runner_result_queue = None

count_lock = threading.Lock()
thread_count = 0

class TestRunnerManager(threading.Thread):
    init_lock = threading.Lock()

    def __init__(self, suite_name, tests_queue, browser_cls, browser_kwargs,
                 executor_cls, executor_kwargs, stop_flag):
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
        self.tests_queue = tests_queue

        self.browser_cls = browser_cls
        self.browser_kwargs = browser_kwargs

        self.executor_cls = executor_cls
        self.executor_kwargs = executor_kwargs

        # Flags used to shut down this thread if we get a sigint
        self.parent_stop_flag = stop_flag
        self.child_stop_flag = multiprocessing.Event()

        global thread_count
        with count_lock:
            self.manager_number = thread_count
            thread_count += 1

        self.command_queue = Queue(name="Command Queue %s" % self.manager_number)
        self.remote_queue = Queue(name="Remote Queue %s" % self.manager_number)

        self.test_runner_proc = None

        threading.Thread.__init__(self, name="TestrunnerManager %i" % self.manager_number)
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
        self.browser = self.browser_cls(self.logger, **self.browser_kwargs)
        try:
            self.init()
            while True:
                commands = {"init_succeeded": self.init_succeeded,
                            "init_failed": self.init_failed,
                            "test_ended": self.test_ended,
                            "restart_test": self.restart_test,
                            "log": self.log,
                            "error": self.error}
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
                            self.logger.debug("No more tests")
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
            self.teardown()
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

            self.browser.start()
            self.start_test_runner()

    def init_succeeded(self):
        """Callback when we have started the browser, connected via
        marionette, and we are ready to start testing"""
        self.logger.debug("Init succeeded")
        self.init_timer.cancel()
        self.init_fail_count = 0
        if self.test is not None:
            return self.start_test(self.test)
        else:
            return self.start_next_test()

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
                                        args=(self.remote_queue,
                                              self.command_queue,
                                              self.browser,
                                              self.executor_cls,
                                              self.executor_kwargs,
                                              self.child_stop_flag),
                                        name="TestRunner %i" % self.manager_number)
        self.test_runner_proc.start()
        self.logger.debug("Test runner started")

    def send_message(self, command, *args):
        self.remote_queue.put((command, args))

    def cleanup(self):
        self.logger.debug("TestManager cleanup")
        while True:
            try:
                self.logger.warning(self.command_queue.get_nowait())
            except Empty:
                break

        while True:
            try:
                self.logger.warning(self.remote_queue.get_nowait())
            except Empty:
                break

    def teardown(self):
        self.logger.info("teardown in testrunnermanager")
        self.logger.info(self.test_runner_proc.is_alive())
        self.test_runner_proc = None
        # self.command_queue.cancel_join_thread()
        # self.remote_queue.cancel_join_thread()
        self.logger.info("Queues empty %s %s" % (self.command_queue.empty(),
                                                 self.remote_queue.empty()))
        self.command_queue.close()
        self.remote_queue.close()
        self.command_queue = None
        self.remote_queue = None

    def stop_runner(self):
        """Stop the TestRunner and the Firefox binary."""
        self.logger.info("Stopping runner")

        try:
            self.browser.stop()
            if self.test_runner_proc:
                self.send_message("stop")
                self.test_runner_proc.join(10)
                if self.test_runner_proc.is_alive():
                    # This might leak a file handle from the queue
                    self.logger.warning("Forcibly terminating runner process")
                    self.test_runner_proc.terminate()
                else:
                    self.logger.info("Testrunner exited with code %i" % self.test_runner_proc.exitcode)
                self.test_runner_proc.join(10)
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
            return Stop
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
        # Write the result of each subtest
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

        # Write the result of the test harness
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
            return self.restart_runner()
        else:
            return self.start_next_test()

    def restart_runner(self):
        """Stop and restart the TestRunner"""
        self.logger.info("Restarting runner")
        self.stop_runner()
        self.init()

    def log(self, level, message):
        getattr(self.logger, level)(message)

    def error(self, message):
        self.logger.error(message)
        self.restart_runner()

class ManagerGroup(object):
    def __init__(self, suite_name, size, browser_cls, browser_kwargs,
                 executor_cls, executor_kwargs):
        """Main thread object that owns all the TestManager threads."""
        self.suite_name = suite_name
        self.size = size
        self.browser_cls = browser_cls
        self.browser_kwargs = browser_kwargs
        self.executor_cls = executor_cls
        self.executor_kwargs = executor_kwargs
        self.pool = set()
        #Event that is polled by threads so that they can gracefully exit in the face
        #of sigint
        self.stop_flag = threading.Event()
        self.logger = structuredlog.StructuredLogger(suite_name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self, tests_queue):
        """Start all managers in the group"""
        self.logger.debug("Using %i processes" % self.size)
        self.tests_queue = tests_queue
        for i in range(self.size):
            manager = TestRunnerManager(self.suite_name,
                                        tests_queue,
                                        self.browser_cls,
                                        self.browser_kwargs,
                                        self.executor_cls,
                                        self.executor_kwargs,
                                        self.stop_flag)
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
        self.logger.debug("Stop flag set in ManagerGroup")

    def unexpected_count(self):
        count = 0
        for item in self.pool:
            count += item.unexpected_count
        return count
