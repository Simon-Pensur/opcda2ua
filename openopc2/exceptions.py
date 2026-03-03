try:
    import Pyro5.api
    PYRO_AVAILABLE = True
except ImportError:
    PYRO_AVAILABLE = False


def _pyro_expose(cls):
    if PYRO_AVAILABLE:
        return Pyro5.api.expose(cls)
    return cls


@_pyro_expose
class TimeoutError(Exception):
    def __init__(self, txt):
        Exception.__init__(self, txt)

    __dict__ = None


@_pyro_expose
class OPCError(Exception):
    def __init__(self, message):
        super(OPCError, self).__init__(self, message)
        self.custom_message = message

    def class_to_dict(self):
        default = self.__dict__
        default["__class__"] = "exceptions.OPCError"
        return default

    @classmethod
    def dict_to_class(cls, class_name, opc_error_dict):
        opc_error_dict.pop("__class__")
        p = OPCError(opc_error_dict.get('custom_message','No message'))
        return p
