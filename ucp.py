#!/usr/bin/env python
# Written for Python 2.7, tested using Python 2.7.5 on CentOS                                         

import socket 
import sys
import time
import random
import hashlib
import binascii
import os
import math


# Configuration
ucpPort = 22222
udpMaxPacketSize = 1500
fileBlockSize = 1024


# Functions
def usage(message):
    sys.stderr.write(message)
    message="\nUsage:   "+sys.argv[0]+' <source> <destination>\n'
    sys.stderr.write(message)
    message="Example: "+sys.argv[0]+' /etc/hostname remotemachine:/tmp/guestHostname\n\n'
    sys.stderr.write(message)
    sys.exit(1)
    
def generateXferId():
    t = long( time.time() * 1000000 )
    r = long( random.random()*100000000000000000L )
    try:
        a = gethostbyname( gethostname() )
    except:
        # if we can't get a network address, just imagine one
        a = random.random()*100000000000000000L
    data = str(t)+' '+str(r)+' '+str(a)
    data = hashlib.sha256(data)
    return data

def parseFileSpec(fileSpec):
    ucpHost,ucpFile = ('','')
    if ':' in fileSpec:
        ucpHost,ucpFile = fileSpec.split(':',1)
    else:
        ucpHost = 'localhost'
        try:
            ucpFile = os.path.abspath(fileSpec)
        except Exception as e:
            message = "Error evaluating local filename: "+fileSpec+"\n"+e+"\n"
            usage(message)
    return ucpHost,ucpFile


# Main - execution starts here
# Evaluate command line arguments
if len(sys.argv) != 3:
    usage("ucp requires exactly 2 arguments")

srcHost,srcFile=parseFileSpec(sys.argv[1])
dstHost,dstFile=parseFileSpec(sys.argv[2])

# Bind to random local port to get responses
r = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
try:
    r.bind(('', 0))
    r.settimeout(2)
except Exception as e:
    log("Error binding to UDP socket")
    log(e)
    exit(1)

myPort = r.getsockname()[1]
#print "Listening on port",myPort



# Send copy initialization request to source machine
s = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
addr = (srcHost,ucpPort)
xferId=generateXferId().hexdigest()
#print "Xfer ID:",xferId
packetData = "ucpInitFileSending\n"+xferId+"\n"+srcFile+"\n"+dstHost+"\n"+dstFile+"\n"+str(myPort)
s.sendto(packetData,addr)

# Init status params
senderStatus = 'Not started'
senderTotalResend = 0
senderSent = 0
senderMessage = ''
receiverStatus = 'Not started'
receiverAcceptedBlocks = 0
receiverMessage = ''
totalBlocks = 0
totalMB = 0.0

print "\n"

while(True):
    try:
        data,sourceIpPort = r.recvfrom(udpMaxPacketSize)
    except socket.timeout:
        packetData = "ucpRequestStatus\n"+xferId+"\n"
        addr = (srcHost,ucpPort)
        s.sendto(packetData,addr)
        addr = (dstHost,ucpPort)
        s.sendto(packetData,addr)
        continue
    except KeyboardInterrupt:
        print "\nSending abort command to daemons"
        packetData = "ucpAbort\n"+xferId+"\n"
        addr = (srcHost,ucpPort)
        s.sendto(packetData,addr)
        time.sleep(1)
        addr = (dstHost,ucpPort)
        s.sendto(packetData,addr)
        exit(1)
    except Exception as e:
        print("Error getting packet")
        print(e)

    pktType,xferIdHex,status = data.split('\n', 2)
    if xferIdHex != xferId:
        continue                        # Drop irrelevant packet
    statusDict = eval(status)
    if statusDict['type'] == 'receive':
        receiverStatus = statusDict['status']
        receiverMessage = statusDict['message']
        if 'progress' in statusDict:
            receiverAcceptedBlocks = statusDict['progress']
        if totalBlocks == 0 and 'fileSize' in statusDict:
            totalBlocks = int(math.ceil(float(statusDict['fileSize'])/fileBlockSize))
            totalMB = float(totalBlocks/1024.0)

    elif statusDict['type'] == 'send':
        senderStatus = statusDict['status']
        senderMessage = statusDict['message']
        if 'progress' in statusDict:
            senderSent = float(int(statusDict['progress'])/1024.0)
        if 'rotalResend' in statusDict:
            senderTotalResend = statusDict['rotalResend']
        if totalBlocks == 0 and 'fileSize' in statusDict:
            totalBlocks = int(math.ceil(float(statusDict['fileSize'])/fileBlockSize))
            totalMB = float(totalBlocks/1024.0)

    if not senderMessage == '':
        senderMessage = "("+senderMessage+")"
    if not receiverMessage == '':
        receiverMessage = "("+receiverMessage+")"
    print '\033[3A'
    print "Received: {0:>10.3f} MB of {1:10.3f} MB   Status: {2} {3}              ".format(float(float(receiverAcceptedBlocks)/1024.0), totalMB, receiverStatus, receiverMessage)
    print "Re-sent:  {0:>10.3f} MB of {1:10.3f} MB   Status: {2} {3}              ".format(float(senderTotalResend/1024.0), senderSent, senderStatus, senderMessage)

    if statusDict['status'] == 'File copied OK':
        packetData = "ucpAckFile\n"+xferId+"\n"
        addr = (srcHost,ucpPort)
        s.sendto(packetData,addr)
        addr = (dstHost,ucpPort)
        s.sendto(packetData,addr)
        s.close()
        r.close()
        print "Copy complete, {} bytes received".format(statusDict['fileSize'])
        exit()

    if statusDict['status'] == 'File hash mismatch, Foo the author':
        print 'File hash mismatch, destination file was not copied correctly'
        packetData = "ucpAbort\n"+xferId+"\n"
        addr = (srcHost,ucpPort)
        s.sendto(packetData,addr)
        time.sleep(1)
        addr = (dstHost,ucpPort)
        s.sendto(packetData,addr)
        exit(1)
        
    if (senderStatus == 'ucpInitFileReceiving sent' and receiverStatus == 'Not started') or (senderStatus == 'Sent file' and receiverStatus == 'ucpSendFile sent'):
        # Restart copy due to lost control packet
        packetData = "ucpAbort\n"+xferId+"\n"
        addr = (srcHost,ucpPort)
        s.sendto(packetData,addr)
        time.sleep(1)
        addr = (dstHost,ucpPort)
        s.sendto(packetData,addr)
        packetData = "ucpInitFileSending\n"+xferId+"\n"+srcFile+"\n"+dstHost+"\n"+dstFile+"\n"+str(myPort)
        addr = (srcHost,ucpPort)
        s.sendto(packetData,addr)


