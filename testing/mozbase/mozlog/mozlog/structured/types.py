class log_action(object):
    def __init__(self, *args):
        self.args = {}

        self.args_no_default = []
        self.args_with_default = []

        for arg in args:
            if arg.default is no_default:
                self.args_no_default.append(arg.name)
            else:
                self.args_with_default.append(arg.name)

            if arg.name in self.args:
                raise ValueError("Repeated argument name %s" % arg.name)

            self.args[arg.name] = arg


    def __call__(self, f):
        action = self

        def inner(self, *args, **kwargs):
            data = {}
            values = {}
            values.update(kwargs)

            j = -1
            for i, arg in enumerate(args):
                j += 1
                # Skip over non-defaulted args supplied as keyword arguments
                while action.args_no_default[j] in values:
                    j += 1
                    if j == len(action.args_no_default):
                        raise TypeError("Too many arguments")
                values[action.args_no_default[j]] = arg

            for k in range(j+1, len(action.args_no_default)):
                if action.args_no_default[k] not in values:
                    raise TypeError("Missing required argument %s\n%r\n%r\n%r" %
                                    (action.args_no_default[k], args, kwargs, values))

            # Fill in missing arguments
            for name in action.args_with_default:
                if name not in values:
                    values[name] = action.args[name].default

            for key, value in values.iteritems():
                out_value = action.args[key](value)
                if out_value is not missing:
                    data[key] = out_value

            return f(self, data)

        return inner

missing = object()
no_default = object()

class DataType(object):
    def __init__(self, name, default=no_default, optional=False):
        self.name = name
        self.default = default

        if default is no_default and optional is not False:
            raise ValueError("optional arguments require a default value")

        self.optional = optional

    def __call__(self, value):
        if value == self.default:
            if self.optional:
                return missing
            return self.default

        try:
            return self.convert(value)
        except:
            import traceback
            print traceback.format_exc()
            raise ValueError("Failed to convert value for field %s to type %s" %
                             (self.name, self.__class__.__name__))

class Unicode(DataType):
    def convert(self, data):
        if isinstance(data, unicode):
            return data
        elif isinstance(data, str):
            return data.encode("utf-8", "replace")
        else:
            return unicode(data)

class TestId(DataType):
    def convert(self, data):
        if isinstance(data, unicode):
            return data
        elif isinstance(data, str):
            return data.encode("utf-8", "replace")
        elif isinstance(data, tuple):
            return tuple(item.encode("utf-8", "replace") for item in data)
        else:
            raise ValueError

class Status(DataType):
    allowed = ["PASS", "FAIL", "OK", "ERROR", "TIMEOUT", "CRASH", "ASSERT", "SKIP"]
    def convert(self, data):
        value = data.upper()
        if value not in self.allowed:
            raise ValueError
        return value

class SubStatus(Status):
    allowed = ["PASS", "FAIL", "ERROR", "TIMEOUT", "CRASH", "ASSERT"]

class Dict(DataType):
    def convert(self, data):
        return dict(data)

class List(DataType):
    def __init__(self, name, item_type, default=no_default, optional=False):
        DataType.__init__(self, name, default, optional)
        self.item_type = item_type(None)

    def convert(self, data):
        return [self.item_type.convert(item) for item in data]
