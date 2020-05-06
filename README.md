# Python UDP file copying daemon and client

This folder contains my submission of a devops scripting task.

## The task:
"***If you please - write me a python UDP file copier***", said the guy at the end of the interview.

What ?

"***Write me a python UDP file copier***"

What kind of copier ? for what environment? Any special requirements ? method of operation ?

"***That doesn't matter. Write me a python UDP file copier***"

![The copier is inside the box](https://i.pinimg.com/236x/78/3f/47/783f470eb29fc9399f369cda870652b6--a-sheep-the-little-prince.jpg)

## Approach Discussion
Given the detailed task definition, along with my my scattered experience using Python, I went ahead to consult my favorite uncle, [Google](https://google.com)

There were several naive ready-made code solutions, but as UDP packets might get lost, corrupted, reordered or duplicated - this is hardly a valid solution.

A different approach would be to use an existing ftp library for python. While it is a valid solution, it mostly shows that one knows that FTP runs over UDP, but it would not really reflect on one's coding abilities.

With the liberty to design and implement as I wish, I chose to take the opportunity and explore some Python programming techniques, including multiprocessing and inter-process communications.

I chose to build **ucp**, a cli and daemon solution with user experience similar to that of scp (and sshd to serve it). [ucpd.py](ucpd.py) is the daemon to be deployed to any participating host, whereas [ucp.py](ucp.py) is the cli to orchestrate the copy operations.  


## Design considerations for ucp

- The code assumes packets may get lost/corrupted/duplicated/out-of-order. 
- With big files in mind, taking into account both performance and disk-space issues, the receiving side assembles the target file directly in-place, while maintaining an in-memory list of received data segments. Any missing blocks are then re-sent on specific request.
- When all the blocks have arrived, a hash of the whole file is verified.
- During the file transfer, the target file get a ".partial" suffix, which is removed after successfully assembling and verifying the file.
- The primary (parent) process of ucpd is responsible for the simplistic handling of incoming packets, so they can be handled as fast as possible, whereas the heavy-lifting is done by ad-hoc child processes.
- Each packet is verified against it's own hash, and is ignored if hash verification fails
- Receiver/Sender flow control is implemented using 2 independent methods:
  - Based on network packet-loss detection (detecting and counting missing packets in the target), the recipient can adjust the sender's sending speed (controlling a delay between groups of packets)
  - Based on high and low watermarks measured on the receiver's incoming udp buffer, the recipient can ask the sender to pause/resume sending
- Inter-Process communications are based on queues (for transferring packet data) and a shared dictionary (for bidirectional status updated)
- Basic logging provided via a centralized log facility to stdout - in a real-life this would be enhanced.
- CLI usage is similar to scp, with an added functionality that one may cope between 2 remote nodes directly without routing traffic via the CLI, which only serves as the orchestrator.
- The CLI provides status and progress indication to the terminal (There's no '-q')
- Python 2.7 was chosen for it's wider support across older operating systems, especially since I was using some ancient server so I can test against a slow recipient...
- CLI keeps watch over the copy process, and may compensate for lost control packets




## Important note for anyone who finds this by chance
While being fully functional, this service is far from being production-grade. It is missing an installer or decent packaging, security features, and substantial testing. In it's current state, it could serve as a fancy security breach, at most :)


## Prerequisites and assumptions
  
  - A Linux operating system.
  - Python 2.7 installed and set as default.  (or change the shebang lines to point to it explicitly)
  
  (Tested on CentOS with Python 2.7.5)


## Submission:
This task is submitted as 2 python 2.7 files and accompanying documentation:

| Filename | Description |
| ------ | ------ |
| [ucpd.py](ucpd.py) | A daemon to serve file copying over UDP to/from a host  |
| [ucp.py](ucp.py) | A CLI interface to ucpd.py
| [README.md](README.md) | This documentation file |
| [daemon-messages.txt](daemon-messages.txt) | Details of internal daemon messages and copy steps |

![Draw me a sheep](https://thelittleprinceinlevels.com/wp-content/uploads/2018/06/LP5-Sheep1.png)




Enjoy :)

  Nadav.

