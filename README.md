# minTorrent
A basic command-line BitTorrent client written in Python. 

minTorrent is an educational project and as such not designed to be a practical BitTorrent client. 

(A work in progress)

##  TODO
* Add support for multi-file torrents
* Add seeding support
* Add tests 

## Getting Started

You will need to have python 3.7+ installed. 

In the project root directory, run the following to install the dependencies:

```
pip3 install -r requirements.txt
```

Then, run the program using:
```
python3 minTorrent/cli.py [path-to-your-torrent-file.torrent]
```
## Contributing

Pull requests are the best way to propose changes to the codebase. We actively welcome your pull requests:

1.  Fork the repo and create your branch from  `master`.
2.  If you've added code that should be tested, add tests.
4.  Ensure the test suite passes.
5.  Make sure your code lints.
6.  Issue that pull request!

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details

## References

* The unofficial BitTorrent protocol specification (much more useful than the official spec): 
	* [https://wiki.theory.org/BitTorrentSpecification](https://wiki.theory.org/BitTorrentSpecification)
* [https://blog.jse.li/posts/torrent/](https://blog.jse.li/posts/torrent/) 



