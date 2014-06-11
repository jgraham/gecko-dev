# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from machformatter import BaseMachFormatter

class TbplFormatter(BaseMachFormatter):
    """Formatter that formats logs in the legacy formatting format used by TBPL
    This is intended to be used to preserve backward compatibility with existing tools
    hand-parsing this format.
    """
    def __init__(self):
        self.suite_start_time = None
        self.test_start_times = {}

    def __call__(self, data):
        return getattr(self, data["action"])(data)

    def log(self, data):
        if data.get('component'):
            return "%s | %s | %s\n" % (data["level"], data["component"], data["message"])

        return "%s | %s\n" % (data["level"] | data["message"])

    def process_output(self, data):
        return "PROCESS | %(process)s | %(data)s\n" % data

    def crash(self, data):
        id = self.id_str(data["test"]) if "test" in data else "pid: " % data["process"]

        rv = ["PROCESS-CRASH | %s | application crashed [%s]" % (id,
                                                                 data["top_frame"])]
        rv.append("Crash dump filename: %s" % data["minidump_path"])
        if data.get("stackwalk_stderr"):
            rv.append("stderr from minidump_stackwalk:")
            rv.append(data["stackwalk_stderr"])
        else:
            rv.append(data["stackwalk_stdout"])
        if data.get("stackwalk_returncode", 0) != 0:
            rv.append("minidump_stackwalk exited with return code %d" %
                      data["stackwalk_returncode"])
        if data.get("stackwalk_errors"):
            rv.extend("stackwalk_errors")
        rv = "\n".join(rv)
        if not rv[-1] == "\n":
            rv += "\n"
        return rv

    def suite_start(self, data):
        self.suite_start_time = data["time"]
        return "SUITE-START | Running %i tests\n" % len(data["tests"])

    def test_start(self, data):
        self.test_start_times[self.test_id(data["test"])] = data["time"]

        return "TEST-START | %s\n" % self.id_str(data["test"])

    def test_status(self, data):
        if "expected" in data:
            return "TEST-UNEXPECTED-%s | %s | %s | expected %s | %s\n" % (
                data["status"], self.id_str(data["test"]), data["subtest"], data["expected"],
                data.get("message", ""))
        else:
            return "TEST-%s | %s | %s | %s\n" % (
                data["status"], self.id_str(data["test"]), data["subtest"], data.get("message", ""))

    def test_end(self, data):
        start_time = self.test_start_times.pop(self.test_id(data["test"]))
        time = data["time"] - start_time

        if "expected" in data:
            return """TEST-END | TEST-UNEXPECTED-%s | %s | expected %s | %s | took %ims\n""" % (
                data["status"], self.id_str(data["test"]), data["expected"],
                data.get("message", ""), time)
        else:
            return "TEST-END | TEST-%s | %s | took %ims\n" % (
                data["status"], self.id_str(data["test"]), time)

    def suite_end(self, data):
        start_time = self.suite_start_time
        time = int((data["time"] - start_time) / 1000)

        return "SUITE-END | took %is\n" % time

    def test_id(self, test_id):
        if isinstance(test_id, (str, unicode)):
            return test_id
        else:
            return tuple(test_id)

    def id_str(self, test_id):
        if isinstance(test_id, (str, unicode)):
            return test_id
        else:
            return " ".join(test_id)
