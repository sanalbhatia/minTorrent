import requests
import torrent
import random
import bencodepy
import socket
from typing import List
from collections import namedtuple
from struct import unpack

from torrent import Torrent

Peer = namedtuple('Peer', ['ip', 'port', 'id'])

class TrackerResponse:
    """
    Represents a tracker response for a torrent.
    """
    def __init__(self, response: dict):
        self.response_raw = response
        self.peers = self._generate_peer_list(response.get(b'peers', ""))
        

    @property
    def failure(self):
        """
        If this response was a failed response, this is the error message to
        why the tracker request failed.
        If no error occurred this will be None
        """
        if b'failure reason' in self.response_raw:
            return self.response_raw[b'failure reason'].decode('utf-8')
        return None
    
    @property
    def interval(self) -> int:
        """
        Interval in seconds that the client should wait between sending
        periodic requests to the tracker.
        """
        return self.response_raw.get(b'interval', 0)

    @property
    def complete(self) -> int:
        """
        Number of peers with the entire file, i.e. seeders.
        """
        return self.response_raw.get(b'complete', 0)
    
    @property
    def incomplete(self) -> int:
        """
        Number of non-seeder peers, aka "leechers".
        """
        return self.response_raw.get(b'incomplete', 0)

    def _generate_peer_list(self, peers_binary) -> List:
        """
        Generates a list of peer ids from given binary 
        The peers may be represented in the binary in two models:
         - binary model:
            peers_binary is a string consisting of multiples of 6 bytes
            the first 4 byets are the IP address and the last 2 are
            the port number (big endian notation)
         - dictionary model:
            peers_binary is a list of dictionaries, each with keys peer id,
            ip and port 
        """
        
        if type(peers_binary) == list:
            #TODO implement support for dictionary peer list
            raise NotImplementedError()
        else:
            # Split the string in pieces of length 6 bytes, where the first
            # 4 characters is the IP the last 2 is the TCP port.
            peers = [peers_binary[i:i+6] for i in range(0, len(peers_binary), 6)]

            #convert encoded adress to list of tuples
            tuple_list = []
            for p in peers:
                tuple_list.append(
                    Peer(
                        socket.inet_ntoa(p[:4]),
                        _decode_port(p[4:]),
                        None
                    )
                )
            return tuple_list

class Tracker:
    """
    Represents the connection to a tracker for a given Torrent.
    """

    def __init__(self, torrent):
        self.torrent = torrent
        self.peer_id = _calculate_peer_id()


    def connect(self, 
                first: bool = None,
                uploaded: int = 0,
                downloaded: int = 0) -> TrackerResponse:
        """
        Makes the announce call to the tracker to update with our 
        statistics as well as get a list of available peers to connect
        to.

        :param first: Whether or not this is the first announce call
        :param uploaded: The total number of bytes uploaded so far
        :parm downloaded: The total number of bytes downloaded so far
        """

        params = {
            "info_hash": self.torrent.info_hash,
            "peer_id": self.peer_id,
            "port": 6889,
            "uploaded": str(uploaded),
            "downloaded": str(downloaded),
            "compact": 1,
            "left": self.torrent.total_size - downloaded
        }
        if first:
            params['event'] = 'started'
        self.response = self._decode(requests.get(self.torrent.announce, params=params).content)

        return TrackerResponse(self.response)

    def _decode(self, txt) -> dict:
        """
        Utility function that decodes bencoded text 
        into ordered dict.
        """
        bc = bencodepy.Bencode( encoding=None,
                                encoding_fallback=None, 
                                dict_ordered=True,
                                dict_ordered_sort=True)
        return bc.decode(txt) 


def _calculate_peer_id() -> str:
    """
    Utility function to calculate peer id.
    Peer id can be any string of length 20 bytes.
    """
    return '-MT0001-' + ''.join(
        [str(random.randint(0, 9)) for _ in range(12)])

def _decode_port(port):
    """
    Converts a 32-bit packed binary port number to int
    """
    # Convert from C style big-endian encoded as unsigned short
    return unpack(">H", port)[0]

if __name__ == "__main__":

    file_path = '../test_files/Haywyre - Panorama_ Discover.torrent'
    torrent = Torrent(file_path)
    
    tracker = Tracker(torrent)
    response = tracker.connect()
    #response from tracker
    print(response.peers)

