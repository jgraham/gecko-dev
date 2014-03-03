# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from __future__ import unicode_literals

import sys
import os
import urlparse
import json
import threading
from multiprocessing import Queue
import hashlib
from collections import defaultdict
import logging
import traceback
from StringIO import StringIO

from mozlog.structured import structuredlog, commandline
from mozlog.structured.handlers import StreamHandler
from mozlog.structured.formatters import JSONFormatter
from mozprocess import ProcessHandler

from testrunner import TestRunner, ManagerGroup
import browser
import metadata
import manifestexpected
import wpttest
import wptcommandline

here = os.path.split(__file__)[0]

# TODO
# Multiplatform expectations
# Documentation
# HTTP server crashes

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

logger = None


def setup_logging(args, defaults):
    global logger
    setup_compat_args(args)
    logger = commandline.setup_logging("web-platform-tests", args, defaults)
    setup_stdlib_logger()

    for name in args.keys():
        if name.startswith("log_"):
            args.pop(name)

    return logger


def setup_stdlib_logger():
    logging.root.handlers = []
    logging.root = structuredlog.std_logging_adapter(logging.root)


def do_test_relative_imports(test_root):
    global serve

    sys.path.insert(0, os.path.join(test_root))
    sys.path.insert(0, os.path.join(test_root, "tools", "scripts"))
    import serve


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
                self.send_message("log", "info",
                                  "The following %i reference urls appear to be equivalent:\n %s" %
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
        proc = ProcessHandler([self.binary, urlparse.urljoin(self.http_server_url, test.url)],
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

class TestChunker(object):
    def __init__(self, manifest, total_chunks, chunk_number):
        self.total_chunks = total_chunks
        self.manifest = manifest
        self.chunk_number = chunk_number
        assert self.chunk_number <= self.total_chunks

    def __iter__(self):
        raise NotImplementedError

class Unchunked(TestChunker):
    def __init__(self, *args, **kwargs):
        TestChunker.__init__(self, *args, **kwargs)
        assert self.total_chunks == 1

    def __iter__(self):
        for item in self.manifest:
            yield item

class HashChunker(TestChunker):
    def __iter__(self):
        chunk_index = self.chunk_number - 1
        for test_path, tests in self.manifest:
            if hash(test_path) % self.total_chunks == chunk_index:
                yield test_path, tests

class EqualTimeChunker(TestChunker):
    """Chunker that uses the test timeout as a proxy for the running time of the test"""
    def __init__(self, *args, **kwargs):
        TestChunker.__init__(self, *args, **kwargs)
        self.indicies = self._make_chunks()

    def _make_chunks(self):
        # For each directory containing tests, calculate the mzximum execution time after running all
        # the tests in that directory. Then work out the index into the manifest corresponding to the
        # directories at fractions of m/N of the running time where m=1..N-1 and N is the total number
        # of chunks. Return an array of these indicies

        total_time = 0
        path_times = []
        manifest_len = 0

        for i, (test_path, tests) in enumerate(self.manifest):
            if list(tests)[0].item_type in ("manual", "helper"):
                continue

            test_dir = tuple(os.path.split(test_path)[0].split(os.path.sep)[:3])

            # Path times is an array of (path, index at start, time at end)
            if not path_times:
                assert i == 0
                path_times.append([test_dir, 0, 0])
            elif path_times[-1][0] != test_dir:
                path_times.append([test_dir, i, path_times[-1][-1]])

            for test in tests:
                timeout = wpttest.LONG_TIMEOUT if test.timeout == "long" else wpttest.DEFAULT_TIMEOUT
                path_times[-1][-1] += timeout
                total_time += timeout

            manifest_len += 1

        time_per_chunk = float(total_time) / self.total_chunks

        indicies = [0]
        next_time = time_per_chunk
        for i, path_time in enumerate(path_times):
            if (path_times[i+1][2] > next_time and
                path_times[i][2] <= next_time):
                index = path_times[i+1][1] if (abs(path_times[i+1][2] - next_time) <
                                               abs(path_times[i][2] - next_time)) else path_time[1]
                indicies.append(index)
                next_time += time_per_chunk
                if len(indicies) == self.total_chunks:
                    break
        indicies.append(manifest_len)

        assert len(indicies) == self.total_chunks + 1

        return indicies

    def __iter__(self):
        low, high = self.indicies[self.chunk_number - 1], self.indicies[self.chunk_number]
        for i, item in enumerate(self.manifest):
            if i == high:
                break
            if i >= low:
                yield item

def queue_tests(test_root, metadata_root, test_types, run_info, include_filters,
                chunk_type, total_chunks, chunk_number):
    """Read in the tests from the manifest file and add them to a queue"""
    test_ids = []
    tests_by_type = defaultdict(Queue)

    metadata.do_test_relative_imports(test_root)
    manifest = metadata.manifest.load(os.path.join(metadata_root, "MANIFEST.json"))

    chunked_manifest = {"none": Unchunked,
                        "hash": HashChunker,
                        "equal_time": EqualTimeChunker}[chunk_type](manifest,
                                                                    total_chunks,
                                                                    chunk_number)

    for test_path, tests in chunked_manifest:
        # This is a very silly way to exclude types
        # but the API in manifest.py should be updated
        test_type = list(tests)[0].item_type
        if test_type not in test_types:
            continue
        expected_file = manifestexpected.get_manifest(metadata_root, test_path, run_info)
        for manifest_test in tests:
            queue_test = False
            if include_filters:
                for filter_str in include_filters:
                    if manifest_test.url.startswith(filter_str):
                        queue_test = True
            else:
                queue_test = True
            if queue_test:
                if expected_file is not None:
                    expected = expected_file.get_test(manifest_test.id)
                else:
                    expected = None
                test = wpttest.from_manifest(manifest_test, expected)
                if not test.disabled():
                    tests_by_type[test_type].put(test)
                    test_ids.append(test.id)

    return test_ids, tests_by_type


class LogThread(threading.Thread):
    def __init__(self, queue, logger, level):
        self.queue = queue
        self.log_func = getattr(logger, level)
        threading.Thread.__init__(self)

    def run(self):
        while True:
            msg = self.queue.get()
            if msg is None:
                break
            else:
                self.log_func(msg)
        self.queue.close()


class LoggingWrapper(StringIO):
    """Wrapper for file like objects to redirect output to logger
    instead"""
    def __init__(self, queue, prefix=None):
        self.queue = queue
        self.prefix = prefix

    def write(self, data):
        if data.endswith("\n"):
            data = data[:-1]
        if data.endswith("\r"):
            data = data[:-1]
        if self.prefix is not None:
            data = "%s: %s" % (self.prefix, data)
        self.queue.put(data)

    def flush(self):
        pass

browser_classes = {"firefox": browser.FirefoxBrowser,
                   "servo": browser.NullBrowser}

test_runner_classes = {"firefox": {"reftest": ReftestTestRunner,
                                   "testharness": TestharnessTestRunner},
                       "servo": {"testharness": ServoTestRunner}}

def run_tests(binary, tests_root, metadata_root, test_types,
              processes=1, include=None, capture_stdio=True, product="firefox",
              chunk_type="none", total_chunks=1, chunk_number=1):
    logging_queue = None
    original_stdio = (sys.stdout, sys.stderr)

    try:
        if capture_stdio:
            logging_queue = Queue()
            logging_thread = LogThread(logging_queue, logger, "info")
            sys.stdout = LoggingWrapper(logging_queue, prefix="STDOUT")
            sys.stderr = LoggingWrapper(logging_queue, prefix="STDERR")
            logging_thread.start()

        do_test_relative_imports(tests_root)

        run_info = wpttest.RunInfo(False)

        logger.info("Using %i client processes" % processes)

        browser_cls = browser_classes[product]

        unexpected_count = 0

        test_queues = None

        with TestEnvironment(tests_root) as test_environment:
            base_server = "http://%s:%i" % (test_environment.config["host"],
                                            test_environment.config["ports"]["http"][0])
            test_ids, test_queues = queue_tests(tests_root, metadata_root,
                                                test_types, run_info, include,
                                                chunk_type, total_chunks, chunk_number)
            logger.suite_start(test_ids, run_info)
            for test_type in test_types:
                tests_queue = test_queues[test_type]
                runner_cls = test_runner_classes[product].get(test_type)

                if runner_cls is None:
                    logger.error("Unsupported test type %s for product %s" % (test_type, product))
                    continue

                with ManagerGroup("web-platform-tests",
                                  runner_cls,
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
    except KeyboardInterrupt:
        if test_queues is not None:
            for queue in test_queues.itervalues():
                queue.close()
                queue.cancel_join_thread()
        sys.exit(1)
    finally:
        if capture_stdio and logging_queue is not None:
            logging_queue.put(None)

        sys.stdout, sys.stderr = original_stdio

    logger.info("Got %i unexpected results" % unexpected_count)

    return manager_group.unexpected_count() == 0


def setup_compat_args(kwargs):
    if not "log_raw" in kwargs or kwargs["log_raw"] is None:
        kwargs["log_raw"] = []

    if "output_file" in kwargs:
        path = kwargs.pop("output_file")
        if path is not None:
            output_dir = os.path.split(path)[0]
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
            kwargs["log_raw"].append(open(path, "w"))

    if "log_stdout" in kwargs:
        if kwargs.pop("log_stdout"):
            kwargs["log_raw"].append(sys.stdout)

def main():
    """Main entry point when calling from the command line"""
    args = wptcommandline.parse_args()
    kwargs = vars(args)

    setup_logging(kwargs, {"raw": sys.stdout})

    return run_tests(**kwargs)
