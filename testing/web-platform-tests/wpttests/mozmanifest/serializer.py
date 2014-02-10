from node import NodeVisitor, ValueNode


def escape(string, extras=""):
    rv = string.encode("utf8").encode("string_escape")
    for extra in extras:
        rv = rv.replace(extra, "\\" + extra)
    return rv


class ManifestSerializer(NodeVisitor):
    def __init__(self, skip_empty_data=False):
        self.skip_empty_data = skip_empty_data

    def serialize(self, root):
        self.indent = 2
        return "\n".join(self.visit(root))

    def visit_DataNode(self, node):
        rv = []
        if not self.skip_empty_data or node.children:
            if node.data:
                rv.append("[%s]" % escape(node.data, extras="]"))
                indent = self.indent * " "
            else:
                indent = ""

            for child in node.children:
                rv.extend("%s%s" % (indent if item else "", item) for item in self.visit(child))

            if node.parent:
                rv.append("")

        return rv

    def visit_KeyValueNode(self, node):
        rv = [node.data + ":"]
        indent = " " * self.indent

        if len(node.children) == 1 and isinstance(node.children[0], ValueNode):
            rv[0] += " %s" % escape(self.visit(node.children[0])[0])
        else:
            for child in node.children:
                rv.append(indent + self.visit(child)[0])

        return rv

    def visit_ValueNode(self, node):
        return [escape(node.data)]

    def visit_ConditionalNode(self, node):
        return ["if %s: %s" % tuple(self.visit(item)[0] for item in node.children)]

    def visit_StringNode(self, node):
        rv = "\"%s\"" % node.data
        for child in node.children:
            rv += self.visit(value)
        return [rv]

    def visit_NumberNode(self, node):
        return [node.data]

    def visit_VariableNode(self, node):
        rv = node.data
        for child in node.children:
            rv += self.visit(child)
        return [rv]

    def visit_IndexNode(self, node):
        assert len(node.children) == 1
        return ["[%s]" % self.visit(node.children[0])[0]]

    def visit_UnaryExpressionNode(self, node):
        return [" ".join(self.visit(item)[0] for item in node.children)]

    def visit_BinaryExpressionNode(self, node):
        assert len(node.children) == 3
        return ["%s %s %s" % (self.visit(node.children[1])[0],
                              self.visit(node.children[0])[0],
                              self.visit(node.children[2])[0])]

    def visit_UnaryOperatorNode(self, node):
        return [node.data]

    def visit_BinaryOperatorNode(self, node):
        return [node.data]

def serialize(tree, *args, **kwargs):
    s = ManifestSerializer(*args, **kwargs)
    return s.serialize(tree)
