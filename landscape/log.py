import inspect
import logging


def format_object(object):
    """
    Returns a fully-qualified name for the specified object, such as
    'landscape.log.format_object()'.
    """
    if inspect.ismethod(object):
        # FIXME If the method is implemented on a base class of
        # object's class, the module name and function name will be
        # from the base class and the method's class name will be from
        # object's class.
        name = repr(object).split(" ")[2]
        return "%s.%s()" % (object.__module__, name)
    elif inspect.isfunction(object):
        name = repr(object).split(" ")[1]
        return "%s.%s()" % (object.__module__, name)
    return "%s.%s" % (object.__class__.__module__, object.__class__.__name__)


def format_delta(seconds):
    if not seconds:
        seconds = 0.0
    return "%.02fs" % float(seconds)


def format_percent(percent):
    if not percent:
        percent = 0.0
    return "%.02f%%" % float(percent)


def rotate_logs():
    """
    This closes and reopens the underlying files in the logging module's
    root logger. If called after logrotate (or something similar) has
    moved the old log file out of the way, this will start writing to a new
    new log file...
    """
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.FileHandler):
            handler.acquire()
            try:
                handler.stream.close()
                handler.stream = open(handler.baseFilename,
                                      handler.mode)
            finally:
                handler.release()
    logging.info("Landscape Logs rotated")
