import sys
import argparse
from collections import defaultdict
import json

from mozlog.structured import reader

class StatusHandler(reader.LogHandler):
    def __init__(self):
        self.run_info = None
        self.statuses = defaultdict(lambda:defaultdict(lambda:defaultdict(lambda: defaultdict(int))))

    def suite_start(self, item):
        self.run_info = tuple(sorted(item.get("run_info", {}).items()))

    def test_status(self, item):
        self.statuses[self.run_info][item["test"]][item["subtest"]][item["status"]] += 1

    def test_end(self, item):
        self.statuses[self.run_info][item["test"]][None][item["status"]] += 1

    def suite_end(self, item):
        self.run_info = None

def get_statuses(filenames):
    handler = StatusHandler()

    for filename in filenames:
        with open(filename) as f:
            reader.handle_log(reader.read(f), handler)

    return handler.statuses

def filter_unstable(statuses):
    unstable = defaultdict(lambda:defaultdict(dict))

    for run_info, tests in statuses.iteritems():
        for test, subtests in tests.iteritems():
            for name, results in subtests.iteritems():
                if len(results) > 1:
                    unstable[run_info][test][name] = results

    return unstable

def print_unstable(unstable):
    for run_info, tests in unstable.iteritems():
        run_str = " ".join("%s:%s" % (k,v) for k,v in run_info) if run_info else "No Run Info"
        print run_str
        print "=" * len(run_str)
        for test, subtests in tests.iteritems():
            print "\n" + test
            print "-" * len(test)
            for name, results in subtests.iteritems():
                print "[%s]: %s" % (name if name is not None else "",
                                    " ".join("%s (%i)" % (k,v) for k,v in results.iteritems()))

def get_parser(add_help=True):
    parser = argparse.ArgumentParser("unstable",
                                     description="List tests that don't give consistent results from one or more runs.", add_help=add_help)
    parser.add_argument("--json", action="store_true", default=False,
                        help="Output in JSON format")
    parser.add_argument("log_file", nargs="+",
                        help="Log files to read")
    return parser

def main(**kwargs):
    unstable = filter_unstable(get_statuses(kwargs["log_file"]))
    if kwargs["json"]:
        print json.dumps(unstable)
    else:
        print_unstable(unstable)

if __name__ == "__main__":
    parser = get_parser()
    args = parser.parse_args()
    kwargs = vars(args)
    main(**kwargs)
