import os
from collections import namedtuple

from mozmanifest.node import DataNode
from mozmanifest.backends import conditional
from mozmanifest.backends.conditional import ManifestItem

import expected

Result = namedtuple("Result", ["run_info", "status"])

def data_cls_getter(output_node, visited_node):
    if output_node is None:
        return ExpectedManifest
    elif isinstance(output_node, ExpectedManifest):
        return TestNode
    elif isinstance(output_node, TestNode):
        return SubtestNode
    else:
        raise ValueError

class ExpectedManifest(ManifestItem):
    def __init__(self, node, test_path=None):
        assert node is None
        node = DataNode(None)
        ManifestItem.__init__(self, node)
        self.child_map = {}
        self.test_path = test_path
        self.modified = False

    def append(self, child):
        ManifestItem.append(self, child)
        self.child_map[child.id] = child
        assert len(self.child_map) == len(self.children)

    def _remove_child(self, child):
        del self.child_map[child.id]
        ManifestItem.remove_child(self, child)
        assert len(self.child_map) == len(self.children)

    def get_test(self, test_id):
        return self.child_map[test_id]

class TestNode(ManifestItem):
    def __init__(self, node):
        ManifestItem.__init__(self, node)
        self.name = node.data
        self.updated_expected = []
        self.new_expected = []
        self.subtests = {}
        self.default_status = None
        self._from_file = True

    @classmethod
    def create(cls, test_type, test_id):
        if test_type == "reftest":
            url = test_id[0]
        else:
            url = test_id
        name = url.split("/")[-1]
        node = DataNode(name)
        self = cls(node)

        self.set("type", test_type)
        if test_type == "reftest":
            self.set("reftype", test_id[1])
            self.set("refurl", test_id[2])
        self._from_file = False
        return self

    @property
    def is_empty(self):
        data_keys = set(self._data.keys())
        required_keys = set(["type"])
        if self.test_type == "reftest":
            required_keys |= set(["reftype", "refurl"])
        if set(self._data.keys()) != required_keys:
            return False
        return all(child.is_empty for child in self.children)

    @property
    def test_type(self):
        return self.get("type", None)

    @property
    def id(self):
        components = self.parent.test_path.split(os.path.sep)[:-1]
        components.append(self.name)
        url = "/" + "/".join(components)
        if self.test_type == "reftest":
            return (url, self.get("reftype", None), self.get("refurl", None))
        else:
            return url

    def disabled(self, run_info):
        return self.get("disabled", run_info) is not None

    def set_result(self, run_info, result):
        found = False

        if self.default_status is not None:
            assert self.default_status == result.default_expected
        else:
            self.default_status = result.default_expected

        for (conditional, values) in self.updated_expected:
            if conditional(run_info):
                values.append(Result(run_info, result.status))
                if result.status != conditional.value:
                    self.root.modified = True
                found = True
                break

        # We didn't find a previous value for this
        if not found:
            self.new_expected.append(Result(run_info, result.status))
            self.root.modified = True

    def coalesce_expected(self):
        final_conditionals = []

        for conditional, values in self.updated_expected:
            if not values:
                final_conditionals.append(conditional)
            elif all(values[0].status == status for run_info, status in values):
                #All the new values for this conditional matched, so update the node
                value = values[0]
                if value == self.default_status:
                    conditional.remove()
                else:
                    conditional.value = values[0][1]
                    final_conditionals.append(conditional)
            else:
                # Blow away the existing condition and rebuild from scratch
                # This isn't sure to work if we have a conditional later that matches
                # these values too, but we can hope, verify that we get the results
                # we expect, and if not let a human sort it out
                conditional.remove()
                self.new_expected.extend(values)

        if self.new_expected:
            if all(self.new_expected[0].status == status
                   for run_info, status in self.new_expected):
                status = self.new_expected[0].status
                if status != self.default_status:
                    self.set("expected", status, condition=None)
                    final_conditionals.append(self._data["expected"][-1])
            else:
                by_status = {(result.status, result.run_info) for result in self.new_expected}
                for conditional, status in group_conditionals(self.new_expected):
                    self.set("expected", status, condition=conditional)
                    final_conditionals.append(ConditionalValue(
                        ManifestCompiler.compile(None, conditional),
                        self._data["expected"].children[-1]))

    def _add_key_value(self, node, values):
        ManifestItem._add_key_value(self, node, values)
        if node.data == "expected":
            self.updated_expected = []
            for value in values:
                self.updated_expected.append((value, []))

    def append(self, node):
        child = ManifestItem.append(self, node)
        self.subtests[child.name] = child

    def get_subtest(self, name):
        if name in self.subtests:
            return self.subtests[name]
        else:
            subtest = SubtestNode.create(name)
            self.append(subtest)
            return subtest

class SubtestNode(TestNode):
    def __init__(self, tree_node):
        TestNode.__init__(self, tree_node)
        self._test = None

    @classmethod
    def create(cls, name):
        node = DataNode(name)
        self = cls(node)
        return self

    @property
    def is_empty(self):
        if self._data:
            return False
        return True

def get_manifest(metadata_root, test_path):
    manifest_path = expected.expected_path(metadata_root, test_path)
    try:
        with open(manifest_path) as f:
            return conditional.compile(f,
                                       data_cls_getter=data_cls_getter,
                                       test_path=test_path)
    except IOError:
        return None
