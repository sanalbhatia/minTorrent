import bencodepy
from hashlib import sha1
from collections import namedtuple
from typing import List, Dict, Tuple

#Represents the files within the torrent (i.e. the files to write to disk)
TorrentFile = namedtuple('TorrentFile', ['path', 'length'])

class Torrent:
    def __init__(self, file_path):
        bc = bencodepy.Bencode( encoding=None,
                                encoding_fallback=None, 
                                dict_ordered=True,
                                dict_ordered_sort=True)
        self.decoded_info = bc.read(file_path)
        #hash the info dict for the tracker
        info = bc.encode(self.decoded_info[b'info'])
        self.info_hash = sha1(info).digest()
        #generate list of files
        self._generate_file_list()
    
    def _generate_file_list(self):
        """
        Stores the list of files included in this torrent.
        """
        self._files = []
        if self.multi_file:
            files_list = self.decoded_info[b'info'][b'files']
            for file in files_list:
                #decode from binary
                path_items = [x.decode('utf-8') for x in file[b'path']]

                #path given as a list of folders and subfolders
                #combine into one string
                path = "/".join(path_items)

                curr_file = TorrentFile(
                    path,
                    file[b'length']
                )
                self._files.append(curr_file)
        else:
            file = TorrentFile(
                self.decoded_info[b'info'][b'name'].decode('utf-8'),
                self.decoded_info[b'info'][b'length']
            )
            self._files.append(file)
    
    @property
    def announce(self) -> str:
        """
        The announce URL to the tracker.
        """
        return self.decoded_info[b'announce'].decode('utf-8')
    
    @property
    def pieces(self) -> List[str]:
        """
        A list of SHA1 hashes corresponding to the pieces of the torrent. 
        In info pieces, the hashes (each 20 bytes) are given as one single string
        read through this and separate each into an item of the array.
        """
        data = self.decoded_info[b'info'][b'pieces']
        pieces = []
        offset = 0
        length = len(data)

        while offset < length:
            pieces.append(data[offset:offset + 20])
            offset += 20
        return pieces 
    
    @property
    def piece_length(self) -> int:
        """
        Length of each piece (in bytes)
        """
        return self.decoded_info[b'info'][b'piece length']

    
    @property
    def files(self) -> [TorrentFile]:
        """
        List of files in the torrent.
        """
        return self._files
    
    @property
    def multi_file(self) -> bool:
        """
        Does this torrent contain multiple files?
        """
        # If the info dict contains a files element then it is a multi-file
        return b'files' in self.decoded_info[b'info']

    @property
    def root_folder(self) -> str:
        """
        Name of the root folder.
        If this is a single file torrent, this will be the name of file to be downloaded.
        """
        return self.decoded_info[b'info'][b'name'].decode('utf-8')
    
    @property
    def total_size(self) -> int:
        """
        The total size (in bytes) of all the files in this torrent.
        """
        size = 0
        for file in self.files:
            size += file.length
        return size

