import struct
import bitstring
import asyncio
from asyncio import Queue
from concurrent.futures import CancelledError

# The default request size for blocks of pieces is 2^14 bytes.
#
# NOTE: The official specification states that 2^15 is the default request
#       size - but in reality all implementations use 2^14. See the
#       unofficial specification for more details on this matter.
#
#       https://wiki.theory.org/BitTorrentSpecification
#
REQUEST_SIZE = 2**14

class ProtocolError(BaseException):
    pass


class PeerConnection:
    """
    A peer connection used to download and upload pieces.

    The peer will consume one available peer from the given queue.
    Based on the peer details the PeerConnection will try to open a 
    connection and perform a BitTorrent handshake.

    After a successful handshake, the PeerConnection will be in a *choked*
    state, not allowed to request any data from the remote peer. After sending
    an interested message the PeerConnection will be waiting to get *unchoked*.

    Once the remote peer has unchoked us, we can start requesting pieces.
    The PeerConnection will continue to request pieces for as long as there
    are pieces left to request, or until the remote peer disconnects.

    If the connection with a remote peer drops, the PeerConnection will consume
    the next available peer from off the queue and try to connect to that one 
    instead.
    """
    def __init__(self, queue: Queue, info_hash,
                peer_id, piece_manager, on_block_cb=None):
        """
        Constructs a PeerConnection and add it to the asyncio event-loop.

        Use `stop` to abort this connection and any subsequent connection
        attempts

        :param queue: The async Queue containing available peers
        :param info_hash: The SHA1 hash for the meta-data's info
        :param peer_id: Our peer ID used to identify ourselves
        :param piece_manager: The manager responsible to determine which pieces
                              to request
        :param on_block_cb: The callback function to call when a block is 
                            received from the remote peer
        """
        self.my_state = []
        self.peer_state = []
        self.queue = queue 
        self.info_hash = info_hash
        self.peer_id = peer_id
        self.remote_id = None
        self.writer = None
        self.reader = None
        self.piece_manager = piece_manager
        self.on_block_cb = on_block_cb
        self.future = asyncio.ensure_future(self._start()) # Start this worker

    async def _start(self):
        while 'stopped' not in self.my_state:
            ip, port, id = await self.queue.get()

            try:
                self.reader, self.writer = await asyncio.open_connection(
                    ip, port
                )

                #initiate handshake
                buffer = await self._handshake()

                #We do not need to be sending BitField because currently we just
                #leech- in order to seed, we need to send a BitField indicating
                #which pieces we have and which we don't

                #The default state for a connection is that peer is not
                #interested and we are choked
                self.my_state.append('choked')

                #Let the peer know we're interested in downloading pieces
                await self._send_interested()
                self.my_state.append('interested')

                #Start reading responses as a stream of messages for as
                #long as the connection is open and data is transmitted 
                iterator = PeerStreamIterator(self.reader, buffer)
                async for message in iterator.anext():
                    if 'stopped' in self.my_state:
                        break
                    if type(message) is BitField:
                        #print("BitField")
                        self.piece_manager.add_peer(self.remote_id,
                                                    message.bitfield)
                    elif type(message) is Interested:
                        #print("Interested")
                        self.peer_state.append('interested')
                    elif type(message) is NotInterested:
                        #print("NotInterested")
                        if 'interested' in self.peer_state:
                            self.peer_state.remove('interested')
                    elif type(message) is Choke:
                        #print("Choke")
                        self.my_state.append('choked')
                    elif type(message) is Unchoke:
                        if 'choked' in self.my_state:
                            self.my_state.remove('choked')
                        #print("UnChoke")
                    elif type(message) is Have:
                        #print("Have")
                        self.piece_manager.update_peer(self.remote_id,
                                                        message.piece_index)
                    elif type(message) is KeepAlive:
                        #print("KeepAlive")
                        pass
                    elif type(message) is Piece:
                        #print("KeepAlive")
                        self.my_state.remove('pending_request')
                        self.on_block_cb(
                            peer_id=self.remote_id,
                            piece_index = message.index,
                            block_offset=message.begin,
                            data=message.block
                        )
                    elif type(message) is Request:
                        #print("Request")
                        #TODO Add support for sending data
                        pass
                    elif type(message) is Cancel:
                        #print("Cancel")
                        #TODO
                        pass
                    
                    #Send block request to remote peer if we're interested
                    if 'choked' not in self.my_state:
                        if 'interested' in self.my_state:
                            if 'pending_request' not in self.my_state:
                                self.my_state.append('pending_request')
                                await self._request_piece()
            except ProtocolError as e:
                print(e)
            except (ConnectionRefusedError, TimeoutError):
                pass
            except (ConnectionResetError, CancelledError):
                pass
            except Exception as e:
                print("An error occured")
                print(e)
                self.cancel()
                raise e 
            self.cancel()

    def cancel(self):
        """
        Sends the cancel message to the remote peer and closes the connection.
        """
        if not self.future.done():
            self.future.cancel()
        if self.writer:
            self.writer.close()
        
        self.queue.task_done()

    def stop(self):
        """
        Stop this connection from the current peer (if a connection exist) and
        from connecting to any new peer.
        """
        #set state to stopped and cancel our future to break out of the loop.
        #The rest of the cleanyp will eventually be managed by loop calling
        #`cancel`
        self.my_state.append('stopped')
        if not self.future.done():
            self.future.cancel()
    
    async def _request_piece(self):
        block = self.piece_manager.next_request(self.remote_id)
        if block:
            message = Request(block.piece, block.offset, block.length).encode()
        
            self.writer.write(message)
            await self.writer.drain()
    
    async def _handshake(self):
        """
        Send the initial handshake to the remote peer and wait for the peer
        to respond with its handshake
        """
        self.writer.write(Handshake(self.info_hash, self.peer_id).encode())
        await self.writer.drain()

        buf = b''
        tries = 1
        while len(buf) < Handshake.length and tries < 10:
            tries += 1
            buf = await self.reader.read(PeerStreamIterator.CHUNK_SIZE)
        
        response = Handshake.decode(buf[:Handshake.length])

        if not response:
            raise ProtocolError('Unable to receive and parse a handshake')
        if not response.info_hash == self.info_hash:
            raise ProtocolError('Handshake with invalid info_hash')

        #TODO: According to the spec we should validate that the peer_id
        # receved from the peer matches the peer_id received from the tracker
        self.remote_id = response.peer_id 

        #We need to return the remaining buffer data, since we might have
        #read more bytes than the size of the handshake message and we need
        #those bytes to parse the next message.
        return buf[Handshake.length:]

    async def _send_interested(self):
        message = Interested()
        self.writer.write(message.encode())
        await self.writer.drain()

class PeerStreamIterator:
    """
    The `PeerStreamIterator` is an async iterator that continuously reads from
    the given stream reader and tries to parse valid BitTorrent messages from 
    the stream of bytes.

    If the connection is dropped or something fails, the iterator will abort
    by raising the `StopAsyncIteration` error ending the calling iteration.
    """
    CHUNK_SIZE = 10*1024

    def __init__(self, reader, initial: bytes=None):
        self.reader = reader
        self.buffer = initial if initial else b''
    
    async def anext(self):
        #Read data from the socket. When we have enough data to parse,
        #parse it and return the message. Until then keep reading
        #from stream
        while True:
            try:
                data = await self.reader.read(PeerStreamIterator.CHUNK_SIZE)
                if data:
                    self.buffer += data
                    message = self.parse()
                    if message:
                        yield message 
                else:
                    # No data read from stream
                    if self.buffer:
                        message = self.parse()
                        if message:
                            yield message
                    raise StopAsyncIteration()
            except ConnectionResetError:
                raise StopAsyncIteration()
            except CancelledError:
                raise StopAsyncIteration()
            except StopAsyncIteration as e:
                # Catch to stop logging
                raise e
            except Exception:
                # TODO: add logging
                raise StopAsyncIteration()
        raise StopAsyncIteration()
    
    def parse(self):
        """
        Tries to parse protocol messages if there is enough bytes read in the
        buffer.
        :return The parsed message, or None if no message could be parsed
        """
        # Each message is structured as:
        #     <length prefix><message ID><payload>
        #
        # The `length prefix` is a four byte big-endian value
        # The `message ID` is a decimal byte
        # The `payload` is the value of `length prefix`
        #
        # The message length is not part of the actual length. So another
        # 4 bytes needs to be included when slicing the buffer.
        header_length = 4

        if len(self.buffer) > 4: #4 bytes is needed to identify the message

            message_length = struct.unpack(">I", self.buffer[0:4])[0]

            if message_length == 0:
                return KeepAlive()
            
            if len(self.buffer) >= message_length:
                message_id = struct.unpack("b", self.buffer[4:5])[0]
                def _consume():
                    """Consume the current message from the read buffer"""
                    self.buffer = self.buffer[header_length + message_length:]

                def _data():
                    """Extract the current message from the read buffer"""
                    return self.buffer[:header_length + message_length]
                
                if message_id is PeerMessage.BitField:
                    data = _data()
                    _consume()
                    return BitField.decode(data)
                elif message_id is PeerMessage.Interested:
                    _consume()
                    return Interested()
                elif message_id is PeerMessage.NotInterested:
                    _consume()
                    return NotInterested()
                elif message_id is PeerMessage.Choke:
                    _consume()
                    return Choke()
                elif message_id is PeerMessage.Unchoke:
                    _consume()
                    return Unchoke()
                elif message_id is PeerMessage.Have:
                    data = _data()
                    _consume()
                    return Have.decode(data)
                elif message_id is PeerMessage.Piece:
                    data = _data()
                    _consume()
                    return Piece.decode(data)
                elif message_id is PeerMessage.Request:
                    data = _data()
                    _consume()
                    return Request.decode(data)
                elif message_id is PeerMessage.Cancel:
                    data = _data()
                    _consume()
                    return Cancel.decode(data)

        return None

#================ Message Classes ================
class PeerMessage:
    """
    A message between two peers.

    All of the remaining messages in the protocol take the form of:
        <length prefix><message ID><payload>
    
    - The length prefix is a four byte big-ending value.
    - The message ID is a single decimal byte.
    - The payload is message dependent

    NOTE: The Handshake message is different in layout compared to the other
          messages.
        
    Read more:
        https://wiki.theory.org/BitTorrentSpecification#Messages
    
    BitTorrent used Big-Endian (Network Byte Order) for all messages, this is
    declared as the first character being '>' in all pack/unpack calls to the 
    Python's 'struct' module.
    """

    Choke = 0
    Unchoke = 1
    Interested = 2
    NotInterested = 3
    Have = 4
    BitField = 5
    Request = 6
    Piece = 7
    Cancel = 8
    Port = 9
    Handshake = None # Handshake is not really part of the messages
    KeepAlive = None # Keep-alive has no ID according to spec

    def encode(self) -> bytes:
        """
        Encodes this object instance to the raw bytes rrepresenting the entire
        message (ready to be transmitted).
        """
        pass 
    
    @classmethod
    def decode(cls, data: bytes):
        """
        Decode the given BitTorrent message into a instance for the
        implementing type.
        """
        pass

class Handshake(PeerMessage):
    """
    The handshake message is the first message sent and then received from a
    remote peer.

    These messages are always 68 bytes long (for this version of BitTorrent 
    protocol).

    Message format:
        <pstrlen><pstr><reserved><info_hash><peer_id>
    
    In version 1.0 of the BitTorrent protocol:
        pstrlen = 19
        pstr = "BitTorrent protocol".
    Thus length is:
        49 + len(pstr) = 68 bytes long.
    """

    length = 49 + 19

    def __init__(self, info_hash: bytes, peer_id: bytes):
        """
        Construct the handshake message

        :param info_hash: The SHA1 hash for the info dict
        :param peer_id: The unique peer id
        """
        if isinstance(info_hash, str):
            info_hash = info_hash.encode('utf-8')
        if isinstance(peer_id, str):
            peer_id = peer_id.encode('utf-8')
        self.info_hash = info_hash
        self.peer_id = peer_id

    def encode(self) -> bytes:
        """
        Encodes this object instance to the raw bytes representing the entire
        message (ready to be transmitted).
        """
        return struct.pack(
            '>B19s8x20s20s',            
            19,                         #Single byte (B)
            b'BitTorrent protocol',     #String (19s)
                                        #Reserved (8x) (pad byte, no value)
            self.info_hash,             #String (20s)
            self.peer_id                #String(20s)
        )
    
    @classmethod
    def decode(cls, data: bytes):
        """
        Decodes the given BitTorrent message into a handshake message, if not
        a valid message, None is returned.
        """
        if len(data) < cls.length:
            return None
        parts = struct.unpack('>B19s8x20s20s', data)
        return cls(info_hash=parts[2], peer_id=parts[3])
    
    def __str__(self):
        return "HandShake"

class KeepAlive(PeerMessage):
    """
    The Keep-Alive message has no payload and length is set to zero.
    Message format:
        <len=0000>
    """
    def __str__(self):
        return "KeepAlive"

class Choke(PeerMessage):
    """
    The choke message is fixed length and has no payload.

    Message format: 
        <len=0001><id=0>
    """
    def __str__(self):
        return "Choke"

class Unchoke(PeerMessage):
    """
    The unchoke message is fixed-length and has no payload.
    
    Message format:
        <len=0001><id=1>
    """
    def __str__(self):
        return "Unchoke"

class Interested(PeerMessage):
    """
    The interested message is fixed-length and has no payload.

    Message format:
        <len=0001><id=2>
    """
    def encode(self) -> bytes:
        """
        Encodes this object instance to the raw bytes representing the entire
        message (ready to be transmitted).
        """
        return struct.pack('>Ib',
                           1,  # Message length
                           PeerMessage.Interested)
    def __str__(self):
        return "Interested"

class NotInterested(PeerMessage):
    """
    The not interested message is fixed-length and has no payload.

    Message format:
        <len=0001><id=3>
    """
    def __str__(self):
        return "NotInterested"

class Have(PeerMessage):
    """
    The have message is fixed length. The payload is the zero-based index
    of a piece that has just been successfully downloaded and verified
    via the hash.

    Message format:
        <len=0005><id=4><piece index>
    """
    def __init__(self, piece_index):
        self.piece_index = piece_index

    def encode(self) -> bytes:
        return struct.pack(
            '>IbI',
            5, # Message length
            PeerMessage.Have, 
            self.piece_index
        )

    @classmethod
    def decode(cls, data: bytes):
        index = struct.unpack('>IbI', data)[2]
        return cls(index)

    def __str__(self):
        return "Have"

class BitField(PeerMessage):
    """
    The bitfield message is variable length, where X is the length of the bitfield.
    The payload is a bit array representing all the bits a peer has (1) or does 
    not have (0).

    Message format:
        <len=0001+X><id=5><bitfield>
    """
    def __init__(self, data):
        self.bitfield = bitstring.BitArray(bytes=data)
    
    def encode(self):
        bits_length = len(self.bitfield)
        return struct.pack(">Ib" + str(bits_length) + 's',
            1 + bits_length,
            PeerMessage.BitField,
            self.bitfield
        )
    
    @classmethod
    def decode(cls, data: bytes):
        message_length = struct.unpack('>I', data[:4])[0]

        parts = struct.unpack(">Ib" + str(message_length-1) + "s", data)
        return cls(parts[2])

    def __str__(self):
        return "BitField"
    
class Request(PeerMessage):
    """
    The request message is fixed length, and is used to request a block
    (i.e. a partial piece).

    The request size for each block is 2^14 bytes, except the final block 
    that might be smaller (since not all pieces might be evenly divided by
    the request size).

    Message format:
        <len=0013><id=6><index><begin><length>
    """
    def __init__(self, index: int, begin: int, length: int = REQUEST_SIZE):
        """
        Constructs the Request message.

        :param index: The zero based piece index
        :param begin: The zero based offset within a piece
        :param length: The requested length of data (default 2 ^ 14)
        """
        self.index = index
        self.begin = begin
        self.length = length
        
    def encode(self):
        return struct.pack(">IbIII",
            13,
            PeerMessage.Request,
            self.index,
            self.begin,
            self.length
        )
    
    @classmethod
    def decode(cls, data: bytes):
        parts = struct.unpack('>IbIII', data)
        return cls(parts[2], parts[3], parts[4])
    
    def __str__(self):
        return "Request"

class Piece(PeerMessage):
    """
    A block is a part of a piece mentioned in the meta-info. The official
    specification refer to them as pieces as well - which is quite confusing
    the unofficial specification refers to them as blocks however.

    So this class is named `Piece` to match the message in the specification
    but really, it represents a `Block` (which is non-existent in the spec).

    Message format:
        <length prefix><message ID><index><begin><block>
    """
    #The Piece message length without the block of data
    length = 9

    def __init__(self, index: int, begin: int, block: bytes):
        """
        Constructs the Piece message.

        :param index: Zero based piece index
        :param begin: Zero based offset within a piece
        :param block: The block data
        """
        self.index = index
        self.begin = begin
        self.block = block 
    
    def encode(self):
        message_length = Piece.length + len(self.block)
        return struct.pack(">IbII" + str(len(self.block)) + "s",
                           message_length,
                           PeerMessage.Piece,
                           self.index,
                           self.begin,
                           self.block
                        )

    @classmethod
    def decode(cls, data: bytes):
        length = struct.unpack('>I', data[:4])[0]
        parts = struct.unpack('>IbII' + str(length - Piece.length) + "s",
                              data[:length+4])
        return cls(parts[2], parts[3], parts[4])

    def __str__(self):
        return 'Piece'

class Cancel(PeerMessage):
    """
    The cancel message is used to cancel a previously requested block (in fact
    the message is identical (besides from the id) to the Request message).

    Message format:
         <len=0013><id=8><index><begin><length>
    """
    def __init__(self, index, begin, length: int = REQUEST_SIZE):
        self.index = index
        self.begin = begin
        self.length = length

    def encode(self):
        return struct.pack('>IbIII',
                           13,
                           PeerMessage.Cancel,
                           self.index,
                           self.begin,
                           self.length)

    @classmethod
    def decode(cls, data: bytes):
        # Tuple with (message length, id, index, begin, length)
        parts = struct.unpack('>IbIII', data)
        return cls(parts[2], parts[3], parts[4])

    def __str__(self):
        return 'Cancel'

class Port(PeerMessage):
    pass
