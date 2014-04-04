# -*- test-case-name: twisted.test.test_amp.TLSTest,twisted.test.test_iosim,twisted.test.test_pb,twisted.protocols.test.test_tls -*-
# Copyright (c) Twisted Matrix Laboratories.
# See LICENSE for details.

"""
Utilities and helpers for simulating a network
"""

from __future__ import print_function

import itertools

try:
    from OpenSSL.SSL import Error as NativeOpenSSLError
except ImportError:
    pass
from twisted.test.proto_helpers import StringTransportWithDisconnection

from zope.interface import implementer, directlyProvides

from twisted.python.failure import Failure
from twisted.internet import error
from twisted.internet import interfaces

class TLSNegotiation:
    def __init__(self, obj, connectState):
        self._obj = obj
        self.connectState = connectState
        self.sent = False
        self.readyToSend = connectState

    def __repr__(self):
        return 'TLSNegotiation(%r)' % (self._obj,)

    def pretendToVerify(self, other, tpt):
        # Set the transport problems list here?  disconnections?
        # hmmmmm... need some negative path tests.

        if not self._obj.iosimVerify(other._obj):
            tpt.disconnectReason = NativeOpenSSLError()
            tpt.loseConnection()



@implementer(interfaces.IAddress)
class FakeAddress(object):
    """
    The default address type for the host and peer of L{FakeTransport}
    connections.
    """



@implementer(interfaces.ITransport,
             interfaces.ITLSTransport)
class FakeTransport(StringTransportWithDisconnection):
    """
    A wrapper around a file-like object to make it behave as a Transport.

    This doesn't actually stream the file to the attached protocol, and is thus
    useful mainly as a utility for debugging protocols.

    @note: This needs to be unified with
        L{twisted.test.proto_helpers.StringTransport}
    @see: U{https://twistedmatrix.com/trac/ticket/5167}
    """

    _nextserial = staticmethod(lambda counter=itertools.count(): next(counter))
    _aborting = True
    _disconnectionReported = False
    disconnecting = 0
    disconnected = 0
    disconnectReason = error.ConnectionDone("Connection done")
    producer = None
    streamingProducer = 0
    _tls = None

    def __init__(self, protocol, isServer, hostAddress=None, peerAddress=None):
        """
        @param protocol: This transport will deliver bytes to this protocol.
        @type protocol: L{IProtocol} provider

        @param isServer: C{True} if this is the accepting side of the
            connection, C{False} if it is the connecting side.
        @type isServer: L{bool}

        @param hostAddress: The value to return from C{getHost}.  C{None}
            results in a new L{FakeAddress} being created to use as the value.
        @type hostAddress: L{IAddress} provider or L{NoneType}

        @param peerAddress: The value to return from C{getPeer}.  C{None}
            results in a new L{FakeAddress} being created to use as the value.
        @type peerAddress: L{IAddress} provider or L{NoneType}
        """
        self.protocol = protocol
        self.isServer = isServer
        self._stream = []
        self.serial = self._nextserial()
        if hostAddress is None:
            hostAddress = FakeAddress()
        self.hostAddress = hostAddress
        if peerAddress is None:
            peerAddress = FakeAddress()
        self.peerAddress = peerAddress


    def __repr__(self):
        return 'FakeTransport<%s,%s,%s>' % (
            self.isServer and 'S' or 'C', self.serial,
            self.protocol.__class__.__name__)


    def write(self, data):
        if self._tls is not None:
            self._tlsbuf.append(data)
        else:
            self._stream.append(data)

    def _checkProducer(self):
        # Cheating; this is called at "idle" times to allow producers to be
        # found and dealt with
        if self.producer:
            self.producer.resumeProducing()

    def registerProducer(self, producer, streaming):
        """From abstract.FileDescriptor
        """
        self.producer = producer
        self._streamingProducer = streaming
        if not streaming:
            producer.resumeProducing()

    def unregisterProducer(self):
        self.producer = None

    def stopConsuming(self):
        self.unregisterProducer()
        self.loseConnection()

    def writeSequence(self, iovec):
        self.write("".join(iovec))


    def loseConnection(self):
        self.disconnecting = True


    def abortConnection(self):
        self._aborted = True
        self.loseConnection()


    def reportDisconnect(self):
        self.disconnecting = True
        if self._tls is not None:
            # We were in the middle of negotiating!  Must have been a TLS problem.
            err = NativeOpenSSLError()
        else:
            err = self.disconnectReason
        self._disconnectionReported = True
        self.protocol.connectionLost(Failure(err))

    def logPrefix(self):
        """
        Identify this transport/event source to the logging system.
        """
        return "iosim"

    def startTLS(self, contextFactory, beNormal=True):
        # Nothing's using this feature yet, but startTLS has an undocumented
        # second argument which defaults to true; if set to False, servers will
        # behave like clients and clients will behave like servers.
        connectState = self.isServer ^ beNormal
        self._tls = TLSNegotiation(contextFactory, connectState)
        self._tlsbuf = []


    def getOutBuffer(self):
        """
        Get the pending writes from this transport, clearing them from the
        pending buffer.

        @return: the bytes written with C{transport.write}
        @rtype: L{bytes}
        """
        S = self._stream
        if S:
            self._stream = []
            return b''.join(S)
        elif self._tls is not None:
            if self._tls.readyToSend:
                # Only _send_ the TLS negotiation "packet" if I'm ready to.
                self._tls.sent = True
                return self._tls
            else:
                return None
        else:
            return None


    def bufferReceived(self, buf):
        if self._disconnectionReported:
            return
        if isinstance(buf, TLSNegotiation):
            assert self._tls is not None # By the time you're receiving a
                                        # negotiation, you have to have called
                                        # startTLS already.
            if self._tls.sent:
                self._tls.pretendToVerify(buf, self)
                self._tls = None # we're done with the handshake if we've gotten
                                # this far... although maybe it failed...?
                # TLS started!  Unbuffer...
                b, self._tlsbuf = self._tlsbuf, None
                self.writeSequence(b)
                directlyProvides(self, interfaces.ISSLTransport)
            else:
                # We haven't sent our own TLS negotiation: time to do that!
                self._tls.readyToSend = True
        else:
            self.protocol.dataReceived(buf)



def makeFakeClient(clientProtocol):
    """
    Create and return a new in-memory transport hooked up to the given protocol.

    @param clientProtocol: The client protocol to use.
    @type clientProtocol: L{IProtocol} provider

    @return: The transport.
    @rtype: L{FakeTransport}
    """
    return FakeTransport(clientProtocol, isServer=False)



def makeFakeServer(serverProtocol):
    """
    Create and return a new in-memory transport hooked up to the given protocol.

    @param serverProtocol: The server protocol to use.
    @type serverProtocol: L{IProtocol} provider

    @return: The transport.
    @rtype: L{FakeTransport}
    """
    return FakeTransport(serverProtocol, isServer=True)



class IOPump(object):
    """
    An L{IOPump} is an in-memory connection between a client L{IProtocol} and a
    server L{IProtocol}.

    @ivar client: The client protocol.
    @type client: L{IProtocol}

    @ivar server: The server protocol.
    @ivar server: L{IProtocol}

    @ivar clientIO: The transport to be associated with C{client}
    @type clientIO: L{FakeTransport}

    @ivar serverIO: The transport to be associated with C{server}
    @type serverIO: L{FakeTransport}

    @ivar debug: Whether or not to print a dump of the traffic to standard
        output.
    @type debug: L{bool}
    """

    def __init__(self, client, server, clientIO, serverIO, debug):
        self.client = client
        self.server = server
        self.clientIO = clientIO
        self.serverIO = serverIO
        self.debug = debug

    def flush(self, debug=False):
        """Pump until there is no more input or output.

        Returns whether any data was moved.
        """
        result = False
        for x in range(1000):
            if self.pump(debug):
                result = True
            else:
                break
        else:
            assert 0, "Too long"
        return result


    def pump(self, debug=False):
        """
        Move data back and forth.

        Returns whether any data was moved.
        """
        debug = self.debug or debug
        if debug:
            print('-- GLUG --')
        sData = self.serverIO.getOutBuffer()
        cData = self.clientIO.getOutBuffer()
        self.clientIO._checkProducer()
        self.serverIO._checkProducer()
        if cData:
            if debug:
                print("C:", repr(cData))
            self.serverIO.bufferReceived(cData)
        if sData:
            if debug:
                print("S:", repr(sData))
            self.clientIO.bufferReceived(sData)
        if cData or sData:
            return True
        if (self.serverIO.disconnecting and
            not self.serverIO.disconnected):
            if debug:
                print('* C')
            self.serverIO.disconnected = True
            self.clientIO.reportDisconnect()
            return True
        if self.clientIO.disconnecting and not self.clientIO.disconnected:
            if debug:
                print('* S')
            self.clientIO.disconnected = True
            self.serverIO.reportDisconnect()
            return True
        return False



def connect(serverProtocol, serverTransport,
            clientProtocol, clientTransport, debug=False, kickoff=True):
    """
    Create a new L{IOPump} connecting two protocols.

    @param serverProtocol: The protocol to use on the accepting side of the
        connection.
    @type serverProtocol: L{IProtocol} provider

    @param serverTransport: The transport to associate with C{serverProtocol}.
    @type serverTransport: L{FakeTransport}

    @param clientProtocol: The protocol to use on the initiating side of the
        connection.
    @type clientProtocol: L{IProtocol} provider

    @param clientTransport: The transport to associate with C{clientProtocol}.
    @type clientTransport: L{FakeTransport}

    @param debug: A flag indicating whether to log information about what the
        L{IOPump} is doing.
    @type debug: L{bool}

    @param kickoff: A flag indicating whether to automatically issue a
        L{IOPump.flush} before returning.

    @return: An L{IOPump} which connects C{serverProtocol} and
        C{clientProtocol} and delivers bytes between them when it is pumped.
    @rtype: L{IOPump}
    """
    serverProtocol.makeConnection(serverTransport)
    clientProtocol.makeConnection(clientTransport)
    pump = IOPump(
        clientProtocol, serverProtocol, clientTransport, serverTransport, debug
    )
    if kickoff:
        pump.flush()
    return pump



def connectedServerAndClient(ServerClass, ClientClass,
                             clientTransportFactory=makeFakeClient,
                             serverTransportFactory=makeFakeServer,
                             debug=False, kickoff=True):
    """
    Connect a given server and client class to each other.

    @param ServerClass: a callable that produces the server-side protocol.
    @type ServerClass: 0-argument callable returning L{IProtocol} provider.

    @param ClientClass: like C{ServerClass} but for the other side of the
        connection.
    @type ClientClass: 0-argument callable returning L{IProtocol} provider.

    @param clientTransportFactory: a callable that produces the transport which
        will be attached to the protocol returned from C{ClientClass}.
    @type clientTransportFactory: callable taking (L{IProtocol}) and returning
        L{FakeTransport}

    @param serverTransportFactory: a callable that produces the transport which
        will be attached to the protocol returned from C{ServerClass}.
    @type serverTransportFactory: callable taking (L{IProtocol}) and returning
        L{FakeTransport}

    @param debug: Should this dump an escaped version of all traffic on this
        connection to stdout for inspection?
    @type debug: L{bool}

    @return: the client protocol, the server protocol, and an L{IOPump} which,
        when its C{pump} and C{flush} methods are called, will move data
        between the created client and server protocol instances.
    @rtype: 3-L{tuple} of L{IProtocol}, L{IProtocol}, L{IOPump}
    """
    c = ClientClass()
    s = ServerClass()
    cio = clientTransportFactory(c)
    sio = serverTransportFactory(s)
    return c, s, connect(s, sio, c, cio, debug, kickoff)
