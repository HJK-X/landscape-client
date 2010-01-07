from twisted.trial.unittest import TestCase
from twisted.internet import reactor
from twisted.internet.defer import Deferred
from twisted.internet.protocol import ClientCreator
from twisted.internet.error import ConnectError

from landscape.lib.amp import (
    MethodCallError, MethodCall, get_nested_attr, Method, MethodCallProtocol,
    MethodCallFactory, RemoteObjectCreator)
from landscape.tests.helpers import LandscapeTest


class Words(object):
    """Test class to be used as target object of a L{MethodCallProtocol}."""

    def secret(self):
        raise RuntimeError("I'm not supposed to be called!")

    def empty(self):
        pass

    def motd(self):
        return "Words are cool"

    def capitalize(self, word):
        return word.capitalize()

    def is_short(self, word):
        return len(word) < 4

    def concatenate(self, word1, word2):
        return word1 + word2

    def lower_case(self, word, index=None):
        if index is None:
            return word.lower()
        else:
            return word[:index] + word[index:].lower()

    def multiply_alphabetically(self, word_times):
        result = ""
        for word, times in sorted(word_times.iteritems()):
            result += word * times
        return result

    def translate(self, word, language):
        if word == "hi" and language == "italian":
            return "ciao"
        else:
            raise RuntimeError("'%s' doesn't exit in %s" % (word, language))

    def meaning_of_life(self):

        class Complex(object):
            pass
        return Complex()

    def _check(self, word, seed, value=3):
        if seed == "cool" and value == 4:
            return "Guessed!"

    def guess(self, word, *args, **kwargs):
        return self._check(word, *args, **kwargs)

    def google(self, word):
        deferred = Deferred()
        if word == "Landscape":
            reactor.callLater(0.01, lambda: deferred.callback("Cool!"))
        elif word == "Easy query":
            deferred.callback("Done!")
        elif word == "Weird stuff":
            reactor.callLater(0.01, lambda: deferred.errback(Exception("bad")))
        elif word == "Censored":
            deferred.errback(Exception("very bad"))
        elif word == "Long query":
            # Do nothing, the deferred won't be fired at all
            pass
        return deferred


class WordsProtocol(MethodCallProtocol):

    methods = [Method("empty"),
               Method("motd"),
               Method("capitalize"),
               Method("is_short"),
               Method("concatenate"),
               Method("lower_case"),
               Method("multiply_alphabetically"),
               Method("translate", language="factory.language"),
               Method("meaning_of_life"),
               Method("guess"),
               Method("google")]


class MethodCallProtocolTest(LandscapeTest):

    def setUp(self):
        super(MethodCallProtocolTest, self).setUp()
        socket = self.mktemp()
        factory = MethodCallFactory(reactor, Words())
        factory.protocol = WordsProtocol
        factory.language = "italian"
        self.port = reactor.listenUNIX(socket, factory)

        def set_protocol(protocol):
            self.protocol = protocol

        connector = ClientCreator(reactor, MethodCallProtocol, reactor)
        connected = connector.connectUNIX(socket)
        return connected.addCallback(set_protocol)

    def tearDown(self):
        self.protocol.transport.loseConnection()
        self.port.loseConnection()
        super(MethodCallProtocolTest, self).tearDown()

    def test_with_forbidden_method(self):
        """
        If a method is not included in L{MethodCallProtocol.methods} it
        can't be called.
        """
        result = self.protocol.callRemote(MethodCall,
                                          name="secret")
        return self.assertFailure(result, MethodCallError)

    def test_with_no_arguments(self):
        """
        A connected client can issue a L{MethodCall} without arguments and
        with an empty response.
        """
        result = self.protocol.callRemote(MethodCall,
                                          name="empty")
        return self.assertSuccess(result, {"result": None,
                                           "deferred": None})

    def test_with_return_value(self):
        """
        A connected client can issue a L{MethodCall} targeted to an
        object method with a return value.
        """
        result = self.protocol.callRemote(MethodCall,
                                          name="motd")
        return self.assertSuccess(result, {"result": "Words are cool",
                                           "deferred": None})

    def test_with_one_argument(self):
        """
        A connected AMP client can issue a L{MethodCall} with one argument and
        a response value.
        """
        result = self.protocol.callRemote(MethodCall,
                                          name="capitalize",
                                          args=["john"])
        return self.assertSuccess(result, {"result": "John",
                                           "deferred": None})

    def test_with_boolean_return_value(self):
        """
        The return value of a L{MethodCall} argument can be a boolean.
        """
        result = self.protocol.callRemote(MethodCall,
                                          name="is_short",
                                          args=["hi"])
        return self.assertSuccess(result, {"result": True,
                                           "deferred": None})

    def test_with_many_arguments(self):
        """
        A connected client can issue a L{MethodCall} with many arguments.
        """
        result = self.protocol.callRemote(MethodCall,
                                          name="concatenate",
                                          args=["You ", "rock"])
        return self.assertSuccess(result, {"result": "You rock",
                                           "deferred": None})

    def test_with_default_arguments(self):
        """
        A connected client can issue a L{MethodCall} for methods having
        default arguments.
        """
        result = self.protocol.callRemote(MethodCall,
                                          name="lower_case",
                                          args=["OHH"])
        return self.assertSuccess(result, {"result": "ohh",
                                           "deferred": None})

    def test_with_overriden_default_arguments(self):
        """
        A connected client can issue a L{MethodCall} with keyword arguments
        having default values in the target object.  If a value is specified by
        the caller it will be used in place of the default value
        """
        result = self.protocol.callRemote(MethodCall,
                                          name="lower_case",
                                          args=["OHH"],
                                          kwargs={"index": 2})
        return self.assertSuccess(result, {"result": "OHh",
                                           "deferred": None})

    def test_with_dictionary_arguments(self):
        """
        Method arguments passed to a L{MethodCall} can be dictionaries.
        """
        result = self.protocol.callRemote(MethodCall,
                                          name="multiply_alphabetically",
                                          args=[{"foo": 2, "bar": 3}],
                                          kwargs={})
        return self.assertSuccess(result, {"result": "barbarbarfoofoo",
                                           "deferred": None})

    def test_with_protocol_specific_arguments(self):
        """
        A L{Method} can specify additional protocol-specific arguments
        that will be added to the ones provided by the L{MethodCall}.
        """
        result = self.protocol.callRemote(MethodCall,
                                          name="translate",
                                          args=["hi"])
        return self.assertSuccess(result, {"result": "ciao", "deferred": None})

    def test_with_non_serializable_return_value(self):
        """
        If the target object method returns an object that can't be serialized,
        the L{MethodCall} result is C{None}.
        """
        result = self.protocol.callRemote(MethodCall,
                                          name="meaning_of_life")
        return self.assertFailure(result, MethodCallError)


class RemoteObjectTest(LandscapeTest):

    def setUp(self):
        super(RemoteObjectTest, self).setUp()
        socket = self.mktemp()
        factory = MethodCallFactory(reactor, Words())
        factory.protocol = WordsProtocol
        factory.language = "italian"
        self.port = reactor.listenUNIX(socket, factory)

        def set_words(remote):
            self.words = remote

        self.connector = RemoteObjectCreator(reactor, socket)
        connected = self.connector.connect()
        return connected.addCallback(set_words)

    def tearDown(self):
        self.connector.disconnect()
        self.port.loseConnection()
        super(RemoteObjectTest, self).tearDown()

    def test_method_call_sender_with_forbidden_method(self):
        """
        A L{RemoteObject} can send L{MethodCall}s without arguments and withj
        an empty response.
        """
        result = self.words.secret()
        return self.assertFailure(result, MethodCallError)

    def test_with_no_arguments(self):
        """
        A L{RemoteObject} can send L{MethodCall}s without arguments and withj
        an empty response.
        """
        return self.assertSuccess(self.words.empty())

    def test_with_return_value(self):
        """
        A L{RemoteObject} can send L{MethodCall}s without arguments and get
        back the value of the commands's response.
        """
        result = self.words.motd()
        return self.assertSuccess(result, "Words are cool")

    def test_with_one_argument(self):
        """
        A L{RemoteObject} can send L{MethodCall}s with one argument and get
        the response value.
        """
        result = self.words.capitalize("john")
        return self.assertSuccess(result, "John")

    def test_with_one_keyword_argument(self):
        """
        A L{RemoteObject} can send L{MethodCall}s with a named argument.
        """
        result = self.words.capitalize(word="john")
        return self.assertSuccess(result, "John")

    def test_with_boolean_return_value(self):
        """
        The return value of a L{MethodCall} argument can be a boolean.
        """
        return self.assertSuccess(self.words.is_short("hi"), True)

    def test_with_many_arguments(self):
        """
        A L{RemoteObject} can send L{MethodCall}s with more than one argument.
        """
        result = self.words.concatenate("You ", "rock")
        return self.assertSuccess(result, "You rock")

    def test_with_many_keyword_arguments(self):
        """
        A L{RemoteObject} can send L{MethodCall}s with several
        named arguments.
        """
        result = self.words.concatenate(word2="rock", word1="You ")
        return self.assertSuccess(result, "You rock")

    def test_with_default_arguments(self):
        """
        A L{RemoteObject} can send a L{MethodCall} having an argument with
        a default value.
        """
        result = self.words.lower_case("OHH")
        return self.assertSuccess(result, "ohh")

    def test_with_overriden_default_arguments(self):
        """
        A L{RemoteObject} can send L{MethodCall}s overriding the default
        value of an argument.
        """
        result = self.words.lower_case("OHH", 2)
        return self.assertSuccess(result, "OHh")

    def test_with_dictionary_arguments(self):
        """
        A L{RemoteObject} can send a L{MethodCall}s for methods requiring
        a dictionary arguments.
        """
        result = self.words.multiply_alphabetically({"foo": 2, "bar": 3})
        return self.assertSuccess(result, "barbarbarfoofoo")

    def test_with_protocol_specific_arguments(self):
        """
        A L{RemoteObject} can send a L{MethodCall} requiring protocol-specific
        arguments, which won't be exposed to the caller.
        """
        result = self.assertSuccess(self.words.translate("hi"), "ciao")
        return self.assertSuccess(result, "ciao")

    def test_with_generic_args_and_kwargs(self):
        """
        A L{RemoteObject} behaves well with L{MethodCall}s for methods
        having generic C{*args} and C{**kwargs} arguments.
        """
        result = self.words.guess("word", "cool", value=4)
        return self.assertSuccess(result, "Guessed!")

    def test_with_success_full_deferred(self):
        """
        If the target object method returns a L{Deferred}, it is handled
        transparently.
        """
        result = self.words.google("Landscape")
        return self.assertSuccess(result, "Cool!")

    def test_with_failing_deferred(self):
        """
        If the target object method returns a failing L{Deferred}, a
        L{MethodCallError} is raised.
        """
        result = self.words.google("Weird stuff")
        return self.assertFailure(result, MethodCallError)

    def test_with_already_callback_deferred(self):
        """
        The target object method can return an already fired L{Deferred}.
        """
        result = self.words.google("Easy query")
        return self.assertSuccess(result, "Done!")

    def test_with_already_errback_deferred(self):
        """
        If the target object method can return an already failed L{Deferred}.
        """
        result = self.words.google("Censored")
        return self.assertFailure(result, MethodCallError)

    def test_with_deferred_timeout(self):
        """
        If the peer protocol doesn't send a response for a deferred within
        the given timeout, the method call fails.
        """
        self.words.protocol.timeout = 0.1
        result = self.words.google("Long query")
        return self.assertFailure(result, MethodCallError)


class RemoteObjectCreatorTest(LandscapeTest):

    def setUp(self):
        super(RemoteObjectCreatorTest, self).setUp()
        self.socket = self.mktemp()
        self.factory = MethodCallFactory(reactor, Words())
        self.factory.protocol = WordsProtocol
        self.factory.language = "italian"

        # FIX: maybe mocker could be used instead
        self.count = 0

        def connect(oself, *args, **kwargs):
            self.count += 1
            return original_connect(oself, *args, **kwargs)

        original_connect = RemoteObjectCreator.connect
        RemoteObjectCreator.connect = connect
        self.addCleanup(setattr, RemoteObjectCreator, "connect",
                        original_connect)
        self.connector = RemoteObjectCreator(reactor, self.socket)


    def test_connect_error(self):
        """
        If a C{retry_interval} is not given, the C{connect} method simply
        fails
        """
        return self.assertFailure(self.connector.connect(), ConnectError)

    def test_connect_with_retry(self):
        """
        If a C{retry_interval} is passed to L{RemoteObjectCreator.connect},
        then the method will transparently retry to connect.
        """

        def listen():
            self.port = reactor.listenUNIX(self.socket, self.factory)

        def assert_connection(words):
            self.assertEquals(self.count, 4)
            result = words.empty()
            result.addCallback(lambda x: self.connector.disconnect())
            result.addCallback(lambda x: self.port.stopListening())
            return result

        reactor.callLater(0.12, listen)
        connected = self.connector.connect(retry_interval=0.05)
        return connected.addCallback(assert_connection)

    def test_connect_max_retries(self):
        """
        If a C{max_retries} is passed to L{RemoteObjectCreator.connect},
        then the method will give up after that amount of retries.
        """
        connected = self.connector.connect(retry_interval=0.01, max_retries=3)
        self.assertFailure(connected, ConnectError)
        return connected.addCallback(
            lambda x: self.assertEquals(self.count, 3))


class GetNestedAttrTest(TestCase):

    def test_get_nested_attr(self):
        """
        The L{get_nested_attr} function returns nested attributes.
        """

        class Object(object):
            pass
        obj = Object()
        obj.foo = Object()
        obj.foo.bar = 1
        self.assertEquals(get_nested_attr(obj, "foo.bar"), 1)

    def test_get_nested_attr_with_empty_path(self):
        """
        The L{get_nested_attr} function returns the object itself if its
        passed an empty string.
        ."""
        obj = object()
        self.assertIdentical(get_nested_attr(obj, ""), obj)
