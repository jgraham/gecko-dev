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
from collections import defaultdict, OrderedDict
import logging
import traceback
from StringIO import StringIO

from mozlog.structured import structuredlog, commandline
from mozlog.structured.handlers import StreamHandler
from mozlog.structured.formatters import JSONFormatter
from mozprocess import ProcessHandler
import moznetwork

from testrunner import TestRunner, ManagerGroup
from executor import MarionetteTestharnessExecutor, MarionetteReftestExecutor, ServoTestharnessExecutor, B2GMarionetteTestharnessExecutor
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
    def __init__(self, test_path, options):
        """Context manager that owns the test environment i.e. the http and
        websockets servers"""
        self.test_path = test_path
        self.server = None
        self.config = None
        self.options = options if options is not None else {}

    def __enter__(self):
        default_config_path = os.path.join(self.test_path, "config.default.json")
        local_config_path = os.path.join(here, "config.json")

        # TODO Move this into serve.py

        with open(default_config_path) as f:
            default_config = json.load(f)

        with open(local_config_path) as f:
            data = f.read()
            local_config = json.loads(data % self.options)

        config = serve.merge_json(default_config, local_config)
        serve.set_computed_defaults(config)

        serve.logger = serve.default_logger("info")
        self.config, self.servers = serve.start(config)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for scheme, servers in self.servers.iteritems():
            for port, server in servers:
                server.kill()

class TestChunker(object):
    def __init__(self, total_chunks, chunk_number):
        self.total_chunks = total_chunks
        self.chunk_number = chunk_number
        assert self.chunk_number <= self.total_chunks

    def __call__(self, manifest):
        raise NotImplementedError

class Unchunked(TestChunker):
    def __init__(self, *args, **kwargs):
        TestChunker.__init__(self, *args, **kwargs)
        assert self.total_chunks == 1

    def __call__(self, manifest):
        for item in manifest:
            yield item

class HashChunker(TestChunker):
    def __call__(self):
        chunk_index = self.chunk_number - 1
        for test_path, tests in manifest:
            if hash(test_path) % self.total_chunks == chunk_index:
                yield test_path, tests

class EqualTimeChunker(TestChunker):
    """Chunker that uses the test timeout as a proxy for the running time of the test"""

    def _get_chunk(self, manifest_items):
        # For each directory containing tests, calculate the mzximum execution time after running all
        # the tests in that directory. Then work out the index into the manifest corresponding to the
        # directories at fractions of m/N of the running time where m=1..N-1 and N is the total number
        # of chunks. Return an array of these indicies

        total_time = 0
        by_dir = OrderedDict()

        class PathData(object):
            def __init__(self):
                self.time = 0
                self.tests = []

        for i, (test_path, tests) in enumerate(manifest_items):
            test_dir = tuple(os.path.split(test_path)[0].split(os.path.sep)[:3])

            if not test_dir in by_dir:
                by_dir[test_dir] = PathData()

            data = by_dir[test_dir]
            time = sum(test.timeout for test in tests)
            data.time += time
            data.tests.append((test_path, tests))

            total_time += time

        if len(by_dir) < self.total_chunks:
            raise ValueError("Tried to split into %i chunks, but only %i subdirectories included" % (self.total_chunks, len(by_dir)))

        n_chunks = self.total_chunks
        time_per_chunk = float(total_time) / n_chunks

        chunks = []

        # Put any individual dirs with a time greater than the timeout into their own
        # chunk
        while True:
            to_remove = []
            for path, data in by_dir.iteritems():
                if data.time > time_per_chunk:
                    to_remove.append((path, data))
            if to_remove:
                for path, data in to_remove:
                    chunks.append(([path], data.tests))
                    del by_dir[path]

                n_chunks -= len(to_remove)
                total_time -= sum(item[1].time for item in to_remove)
                time_per_chunk = total_time / n_chunks
            else:
                break

        chunk_time = 0
        for i, (path, data) in enumerate(by_dir.iteritems()):
            if i == 0:
                # Always start a new chunk the first time
                chunks.append(([path], data.tests))
                chunk_time = data.time
            elif chunk_time + data.time > time_per_chunk:
                if (abs(time_per_chunk - chunk_time) <=
                    abs(time_per_chunk - (chunk_time + data.time))):
                    # Add a new chunk
                    chunks.append(([path], data.tests))
                    chunk_time = data.time
                else:
                    # Add this to the end of the previous chunk but
                    # start a new chunk next time
                    chunks[-1][0].append(path)
                    chunks[-1][1].extend(data.tests)
                    chunk_time += data.time
            else:
                # Append this to the previous chunk
                chunks[-1][0].append(path)
                chunks[-1][1].extend(data.tests)
                chunk_time += data.time

        assert len(chunks) == self.total_chunks, len(chunks)
        chunks = sorted(chunks)

        return chunks[self.chunk_number - 1][1]


    def __call__(self, manifest_iter):
        manifest = list(manifest_iter)
        tests = self._get_chunk(manifest)
        for item in tests:
            yield item

class ManifestFilter(object):
    def __init__(self, include=None, exclude=None):
        self.include = include if include is not None else []
        self.exclude = exclude if exclude is not None else []

    def __call__(self, manifest_iter):
        for test_path, tests in manifest_iter:
            include_tests = set()
            for test in tests:
                if self.include:
                    include = any(test.url.startswith(inc) for inc in self.include)
                else:
                    include = True
                if self.exclude:
                    exclude = any(test.url.startswith(exc) for exc in self.exclude)
                else:
                    exclude = False
                if include and not exclude:
                    include_tests.add(test)

            if include_tests:
                yield test_path, include_tests

def queue_tests(test_root, metadata_root, test_types, run_info, include_filters,
                chunk_type, total_chunks, chunk_number):
    """Read in the tests from the manifest file and add them to a queue"""
    test_ids = []
    tests_by_type = defaultdict(Queue)

    metadata.do_test_relative_imports(test_root)
    manifest = metadata.manifest.load(os.path.join(metadata_root, "MANIFEST.json"))

    manifest_filter = ManifestFilter(include=include_filters)
    manifest_items = manifest_filter(manifest.itertypes(*test_types))

    chunker = {"none": Unchunked,
               "hash": HashChunker,
               "equal_time": EqualTimeChunker}[chunk_type](total_chunks,
                                                           chunk_number)

    #TODO need to do the filtering of types before the chunker runs
    for test_path, tests in chunker(manifest_items):
        expected_file = manifestexpected.get_manifest(metadata_root, test_path, run_info)
        for manifest_test in tests:
            test_type = manifest_test.item_type
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
        threading.Thread.__init__(self, name="Thread-Log")

    def run(self):
        while True:
            msg = self.queue.get()
            if msg is None:
                break
            else:
                self.log_func(msg)


class LoggingWrapper(StringIO):
    """Wrapper for file like objects to redirect output to logger
    instead"""
    def __init__(self, queue, prefix=None):
        self.queue = queue
        self.prefix = prefix

    def write(self, data):
        if isinstance(data, str):
            data = data.decode("utf8")

        if data.endswith("\n"):
            data = data[:-1]
        if data.endswith("\r"):
            data = data[:-1]
        if not data:
            return
        if self.prefix is not None:
            data = "%s: %s" % (self.prefix, data)
        self.queue.put(data)

    def flush(self):
        pass


def get_browser(product, binary):
    browser_classes = {"firefox": browser.FirefoxBrowser,
                       "servo": browser.NullBrowser,
                       "b2g": browser.B2GBrowser}

    browser_cls = browser_classes[product]

    browser_kwargs = {"binary": binary} if product == "firefox" else {}

    return browser_cls, browser_kwargs

def get_options(product):
    return {"firefox": {"host": "localhost"},
            "servo": {"host": "localhost"},
            "b2g": {"host": moznetwork.get_ip()}}[product]

def get_executor(product, test_type, http_server_url, timeout_multiplier):
    executor_classes = {"firefox": {"reftest": MarionetteReftestExecutor,
                                    "testharness": MarionetteTestharnessExecutor},
                        "servo": {"testharness": ServoTestharnessExecutor},
                        "b2g": {"testharness": B2GMarionetteTestharnessExecutor}}

    executor_cls = executor_classes[product].get(test_type)
    if not executor_cls:
        return None, None

    executor_kwargs = {"http_server_url": http_server_url,
                       "timeout_multiplier":timeout_multiplier}

    return executor_cls, executor_kwargs

def run_tests(tests_root, metadata_root, test_types, binary=None, processes=1,
              include=None, capture_stdio=True, product="firefox",
              chunk_type="none", total_chunks=1, this_chunk=1, timeout_multiplier=1):
    logging_queue = None
    original_stdio = (sys.stdout, sys.stderr)
    test_queues = None

    try:
        if capture_stdio:
            logging_queue = Queue()
            logging_thread = LogThread(logging_queue, logger, "info")
            sys.stdout = LoggingWrapper(logging_queue, prefix="STDOUT")
            sys.stderr = LoggingWrapper(logging_queue, prefix="STDERR")
            logging_thread.start()

        do_test_relative_imports(tests_root)

        run_info = wpttest.get_run_info(product, debug=False)

        logger.info("Using %i client processes" % processes)

        browser_cls, browser_kwargs = get_browser(product, binary)
        env_options = get_options(product)

        unexpected_count = 0

        with TestEnvironment(tests_root, env_options) as test_environment:
            base_server = "http://%s:%i" % (test_environment.config["host"],
                                            test_environment.config["ports"]["http"][0])
            test_ids, test_queues = queue_tests(tests_root, metadata_root,
                                                test_types, run_info, include,
                                                chunk_type, total_chunks, this_chunk)
            logger.suite_start(test_ids, run_info)
            for test_type in test_types:
                tests_queue = test_queues[test_type]

                executor_cls, executor_kwargs = get_executor(product, test_type, base_server,
                                                             timeout_multiplier)

                if executor_cls is None:
                    logger.error("Unsupported test type %s for product %s" % (test_type, product))
                    continue

                with ManagerGroup("web-platform-tests",
                                  processes,
                                  browser_cls,
                                  browser_kwargs,
                                  executor_cls,
                                  executor_kwargs) as manager_group:
                    try:
                        manager_group.start(tests_queue)
                    except KeyboardInterrupt:
                        logger.critical("Main thread got signal")
                        manager_group.stop()
                        raise
                    manager_group.wait()
                unexpected_count += manager_group.unexpected_count()

            logger.suite_end()
    except KeyboardInterrupt:
        if test_queues is not None:
            for queue in test_queues.itervalues():
                queue.cancel_join_thread()
    finally:
        if test_queues is not None:
            for queue in test_queues.itervalues():
                queue.close()
        sys.stdout, sys.stderr = original_stdio
        if capture_stdio and logging_queue is not None:
            logger.info("Closing logging queue")
            logging_queue.put(None)
            logging_thread.join(10)
            logging_queue.close()

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
