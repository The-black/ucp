#!/usr/bin/env python
# 
# Daemon providing file copying over UDP
# Written for Python 2.7, tested using Python 2.7.5 on CentOS

import socket 
import multiprocessing
import sys
import select
import hashlib
import binascii
import os
import time
import traceback
import math
import pdb
import time 
import Queue


# Configuration
listenHost="0.0.0.0"
listenPort = 22222
myPortOffset = 0                # (used for development only)
fileBlockSize = 1024
udpMaxPacketSize = 1500
statusCheckInterval = 256
senderCheckInterval = 16
initialSendDelay = 0.005
printDebug = 0                  # 0 => Off ,  1 => On


# Initialize globals
mp = multiprocessing.Manager()
currentSessions = mp.dict()     # Make this dictionary mutable across parent and children
global q
q = {}
parentPid = os.getpid()
global p
p = {}



# Internal functions
def log(message,level='info'):
    if parentPid == os.getpid():
        print "In parent: ",message
    else:
        print "In child:  ",message
    return


def debug(message):
    if printDebug == 1:
        log(message)
    return


def sendPacket(destIp,destPort,pktContent):
    try:
        sp = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        addr = (destIp,destPort)
        sp.sendto(pktContent,addr)
        sp.close
    except Exception as e:
        log("Error sending packet")
        log(e)
    return


def getUdpBufferUsage():
    with open('/proc/net/udp', 'rt') as fu:
        fu.readline()  # Consume and discard header line
        for line in fu:
            values = line.replace(':', ' ').split()
            if int(values[2], 16) == listenPort:
                return int(values[7], 16)
    return 0


def sendStatus(xferId,message=''):
    if xferId not in currentSessions:
        log("In send status: session "+xferId+" does not exist")
        return

    currentSession = eval(str(currentSessions[xferId]))
    debug('In sendStatus')
    debug(str(currentSession))
    currentSession['message'] = str(message)
    packetData = "ucpStatus\n"+xferId+"\n"+str(currentSession)
    sendPacket(currentSession['clientAddr'],int(currentSession['clientPort']),packetData)
    return


def doReceiverHousekeeping(xferId,numBlocks,segments,grace=0):
    currentSession = eval(str(currentSessions[xferId]))
    if 'calculatedFileHash' in currentSession and 'receivedFileHash' in currentSession:     # Both hashes are here
        if currentSession['receivedFileHash'] == currentSession['calculatedFileHash']:
            currentSession['status'] = 'File copied OK'
            debug('Rename at housekeeping: '+currentSession['recipientFilename'])
            os.rename(currentSession['recipientFilename']+'.partial', currentSession['recipientFilename'])
        else:
            currentSession['status'] = 'File hash mismatch, Foo the author'
        currentSessions[xferId] = eval(str(currentSession))
        sendStatus(xferId)
        debug(segments)
        log('Done comparing hashes in doReceiverHousekeeping')
        return
    
    if getUdpBufferUsage() > udpBufferHouseKeepingWatermark:
        log('UDP buffer watermark too high for ousekeeping, skipping')
        return

    if len(segments) > 1:
        log('In houseKeeping, asking for missing blocks')
        debug(segments)
        log("q size:"+str(q[xferId].qsize()))
        for segment in range(len(segments)-1-grace):
            gapStart = str(segments[segment][1] + 1)
            gapEnd   = str(segments[segment+1][0] - 1)
            sendPacket(currentSession['senderAddr'], listenPort, "ucpResendFilePackets\n"+xferId+"\n"+gapStart+"\n"+gapEnd)
    if numBlocks-1 > segments[-1][1]:
        sendPacket(currentSession['senderAddr'], listenPort, "ucpResendFilePackets\n"+xferId+"\n"+str(segments[-1][1])+"\n"+str(numBlocks-1))
    return


def calculateFileHash(xferId,fileName):
    currentSession = eval(str(currentSessions[xferId]))
    log('In calculateFileHash')
    debug(str(currentSession))
    targetFile = open(fileName,'rb')
    targetFile.seek(0)
    data = targetFile.read(fileBlockSize)
    fileHash = hashlib.sha256()
    currentSession['status'] = 'Calculating hash'
    currentSessions[xferId] = eval(str(currentSession))
    while (data):
        fileHash.update(data)
        data = targetFile.read(fileBlockSize)

    targetFile.close()
    return fileHash.hexdigest()



# Child processes
def runSenderChildProcess(xferId):
    currentSession = eval(str(currentSessions[xferId]))
    log('Sending file: '+currentSession['fileName'])
    f=open(currentSession['fileName'],"rb")
    s = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    addr = (currentSession['recipient'],listenPort)
    data = f.read(fileBlockSize)
    fileHash = hashlib.sha256()
    packetSeq = 0
    currentSession['status'] = 'Sending file'
    currentSessions[xferId] = eval(str(currentSession))
    while (data):
        packetHash = hashlib.sha256(data).hexdigest()
        fileHash.update(data)
        packetData = "ucpReceiveFilePacket\n"+xferId+"\n"+str(packetSeq)+"\n"+packetHash+"\n"+data
        s.sendto(packetData,addr)
        debug(len(data))
        data = f.read(fileBlockSize)
        packetSeq += 1
        if packetSeq%senderCheckInterval == 0:
            currentSession = eval(str(currentSessions[xferId]))
            currentSession['progress'] = str(packetSeq)
            if 'gevald' in currentSession and currentSession['gevald'] == "1":
                log("Gevald, pausing file sending")
                currentSession['status'] = 'Sending file paused'
                currentSessions[xferId] = eval(str(currentSession))
                sendStatus(xferId)
                while currentSessions[xferId]['gevald'] == "1":
                    time.sleep(0.5)
                currentSession = eval(str(currentSessions[xferId]))
                currentSession['status'] = 'Sending file'
                log("UnGevald, resuming file sending")

            time.sleep(float(currentSession['sendDelay']))
            currentSessions[xferId] = eval(str(currentSession))
            sendStatus(xferId)

    f.close()
    currentSession['fileHash'] = str(fileHash.hexdigest())
    packetData = "ucpReceiveFileHash\n"+xferId+"\n"+str(fileHash.hexdigest())
    s.sendto(packetData,addr)
    s.close()
    currentSession['status'] = 'Sent file'
    currentSessions[xferId] = eval(str(currentSession))
    sendStatus(xferId)
    debug(str(currentSession))
    log('Sender child exiting - bye-bey')
    return


def runReceiverChildProcess(xferId):
    currentSession = eval(str(currentSessions[xferId]))
    try:
        targetFile = open(currentSession['recipientFilename']+'.partial',"wb")
    except Exception as e:
        log("Error opening target file "+currentSession['recipientFilename']+'.partial')
        log(e)
        traceback.print_exc()
        currentSessions[xferId] = eval(str(currentSession))
        sendStatus(xferId,"Error opening target file "+currentSession['recipientFilename']+'.partial')
    
    segments = []
    seekBlocks = -1
    lastSegment = -1
    prevLastSegment = 0
    currentSegment = -1
    numBlocks = int(math.ceil(float(currentSession['fileSize'])/fileBlockSize))
    acceptedBlocks = 0
    qTimeout = 10
    duplicates = 0
    while(True):
        try:
            packet = q[xferId].get(timeout=qTimeout)
        except Queue.Empty as e:
            buffSize =getUdpBufferUsage()
            if buffSize > udpBufferHighWatermark:
                packetData = "ucpGevald\n"+xferId+"\n "
                sendPacket(currentSession['senderAddr'],listenPort,packetData)
                gevald = 1
            elif buffSize < udpBufferLowWatermark:
                if gevald == 1:
                    packetData = "ucpUnGevald\n"+xferId+"\n "
                    sendPacket(currentSession['senderAddr'],listenPort,packetData)
                    gevald = 0
                else:
                    packetData = "ucpUnGevald\n"+xferId+"\n "
                    sendPacket(currentSession['senderAddr'],listenPort,packetData)
                    log('Calling housekeeping')
                    doReceiverHousekeeping(xferId,numBlocks,segments)
                    log('HouseKeeping done')

            continue

        except Exception as e:
            log("Error getting message from queue ")
            log(e)
            traceback.print_exc()
            continue 

        qTimeout = 2
        pktSeq,pktHash,pktData = packet.split('\n', 2)
        debug(len(pktData))    
        pktSeq = int(pktSeq)
        if pktSeq < 0 or pktSeq >= numBlocks :
            log("Drop packet due to bad sequence number: "+str(pktSeq))
            continue                   # Drop packet
            
        pktComputedHash = hashlib.sha256(pktData).hexdigest()
        if pktComputedHash != pktHash:
            log("Drop packet due to mismatching hashes")
            continue                   # Drop packet

        # Prepare for writing the file block
        duplicatePacket = 0
        if pktSeq != seekBlocks:
            if seekBlocks == -1:                                                        # First arriving packet of file
                segments.insert(0,[pktSeq, pktSeq])
                lastSegment = 0
                currentSegment = 0
                seekBlocks = pktSeq
                targetFile.seek(pktSeq*fileBlockSize)
            elif segments[0][0] > pktSeq:                                               # packet located before first existing segment
                segments.insert(0,[pktSeq, pktSeq])
                currentSegment = 0
                lastSegment += 1
                seekBlocks = pktSeq
                targetFile.seek(pktSeq*fileBlockSize)
            elif segments[lastSegment][1] == pktSeq-1:                                  # Append to last segment
                currentSegment = lastSegment
                seekBlocks = pktSeq
                targetFile.seek(pktSeq*fileBlockSize)
            elif segments[lastSegment][1] < pktSeq-1:                                   # Append another segment
                lastSegment += 1
                segments.append([pktSeq, pktSeq])
                currentSegment = lastSegment
                seekBlocks = pktSeq
                targetFile.seek(pktSeq*fileBlockSize)
            else:                                                                       # Look for it ...
                for segment in range(lastSegment+1):
                    if segments[segment][1] >= pktSeq:
                        duplicatePacket = 1
                        if segment == lastSegment:
                            debug('duplicate packet '+str(pktSeq)+" "+str(segments[segment][0])+" "+str(segments[segment][1]))
                        else:
                            debug('duplicate packet '+str(pktSeq)+" "+str(segments[segment][0])+" "+str(segments[segment][1])+" "+str(segments[segment+1][0]))
                        break
                    elif segments[segment][1] == pktSeq-1:                              # Append to this segment
                        currentSegment = segment
                        seekBlocks = pktSeq
                        targetFile.seek(pktSeq*fileBlockSize)
                        break
                    elif segment < lastSegment and segments[segment+1][0] <= pktSeq:    # Next segment please
                        continue
                    else:                                                               # insert a new segment
                        segments.insert(segment+1,[pktSeq, pktSeq])
                        currentSegment = segment+1
                        lastSegment += 1
                        seekBlocks = pktSeq
                        targetFile.seek(pktSeq*fileBlockSize)
                        break
        
        if duplicatePacket == 1:                                                        # Skip duplicate packets
            duplicates += 1
            continue
        
        # Write the file block
        try:
            targetFile.write(pktData)
        except Exception as e:
            log("Error writing to file "+currentSession['recipientFilename'])
            log(e)
            traceback.print_exc()
            continue
                        
        seekBlocks += 1
        segments[currentSegment][1] = pktSeq
        acceptedBlocks += 1
        if acceptedBlocks%statusCheckInterval == 0:                         # Set interval for status reporting
            currentSession = eval(str(currentSessions[xferId]))
            debug("In update progress, session is:"+str(currentSession))
            currentSession['status'] = 'Receiving file'
            currentSession['progress'] = str(acceptedBlocks)
            currentSessions[xferId] = eval(str(currentSession))
            debug("q:"+str(q[xferId].qsize())+'   Blocks accepted: '+str(acceptedBlocks)+' / '+str(numBlocks)+'  segment '+str(currentSegment)+' / '+str(len(segments)-1)+'  Last: '+str(pktSeq)+'  Dups: '+str(duplicates))
            sendStatus(xferId)

            # Send flow control signals if necessary
            buffSize =getUdpBufferUsage() 
            if buffSize > udpBufferHighWatermark:
                packetData = "ucpGevald\n"+xferId+"\n "
                sendPacket(currentSession['senderAddr'],listenPort,packetData)
                gevald = 1

            if buffSize < udpBufferLowWatermark:
                packetData = "ucpUnGevald\n"+xferId+"\n "
                sendPacket(currentSession['senderAddr'],listenPort,packetData)
                gevald = 0

            if lastSegment == 0:      # Ask to speed-up :)
                packetData = "ucpSpeedUp\n"+xferId+"\n "
                sendPacket(currentSession['senderAddr'],listenPort,packetData)
            elif lastSegment-prevLastSegment > 5:     #  new segments, ask to slow down
                packetData = "ucpSlowDown\n"+xferId+"\n "
                sendPacket(currentSession['senderAddr'],listenPort,packetData)
                prevLastSegment = lastSegment
                
        # Merge adjoining segments 
        if currentSegment < lastSegment and segments[currentSegment+1][0] == pktSeq+1:
            segments[currentSegment][1] = segments[currentSegment+1][1]
            del segments[currentSegment+1]
            lastSegment -= 1

        if segments[0][0] == 0 and segments[0][1] == numBlocks-1:
            # All packets are here, calculate file hash
            targetFile.close()
            currentSession['calculatedFileHash'] = str(calculateFileHash(xferId,currentSession['recipientFilename']+'.partial'))
            if 'receivedFileHash' in currentSessions[xferId]:   # Hash already received by parent
                currentSession['receivedFileHash']=currentSessions[xferId]['receivedFileHash']
                if currentSession['receivedFileHash'] == currentSession['calculatedFileHash']:
                    currentSession['status'] = 'File copied OK'
                    debug('Rename at end of child: '+currentSession['recipientFilename'])
                    os.rename(currentSession['recipientFilename']+'.partial', currentSession['recipientFilename'])
                else:
                    currentSession['status'] = 'File hash mismatch, Foo the author'
            else:                                               # Hash not reeived yet
                currentSession['status'] = 'Waiting for hash'
                # Hash may suffer from a race condition between parent and child.
                # Ask for a re-send anyway so as not to wait for next timeout
                packetData = "ucpResendFileHash\n"+xferId+"\n "
                sendPacket(currentSession['senderAddr'],listenPort,packetData)

            currentSessions[xferId] = eval(str(currentSession))
            sendStatus(xferId)
            log('Receiver child exiting, bye-bye')
            return



# Incoming packets handling
def handleInitFileSending(xferId,pktData):
    if xferId in currentSessions:
        log("Session "+xferId+" already exists")
        sendStatus(xferId, "Session "+xferId+" already exists")
        return
    
    try:
        fileName,recipient,recipientFilename,cliReturnPort = pktData.split('\n', 3)
    except Exception as e:
        log("Error parsing packet")
        log(e)
        return

    fileName = str(os.path.realpath(str(fileName)))    # Use actual file and not any link to it
    recipient = str(recipient)
    if recipient == 'localhost':
        recipient = str(sourceIp)
    recipientFilename = str(recipientFilename)
    cliReturnPort = str(cliReturnPort)
    log("Filename: "+fileName)
    log("Recipient: "+recipient)
    log("Recipient filename:"+recipientFilename)
    log("Client address: "+sourceIp)
    log("Client return port: "+cliReturnPort)

    currentSession = {  'type'              : 'send',                \
                        'fileName'          : fileName,              \
                        'recipient'         : recipient,             \
                        'recipientFilename' : recipientFilename,     \
                        'clientAddr'        : str(sourceIp),         \
                        'clientPort'        : cliReturnPort,         \
                        'sendDelay'         : str(initialSendDelay), \
                        'status'            : 'Sender initializing'  }

    if not ( os.path.isfile(fileName) and os.access(fileName,os.R_OK)):
        currentSession['status'] = 'failed to read source file'+fileName
        currentSessions[xferId] = eval(str(currentSession))
        log(currentSession['status'])
        sendStatus(xferId)
        return

    try:
        currentSession['size'] = str(os.stat(fileName).st_size)
    except Exception as e:
        log("Error getting file size for "+fileName)
        log(e)
        return

    # Send copy initialization request to target machine
    packetData = "ucpInitFileReceiving\n"+xferId+"\n"+recipientFilename+"\n"+currentSession['size']+"\n"+sourceIp+"\n"+cliReturnPort
    sendPacket(recipient,listenPort,packetData)

    currentSession['status'] = 'ucpInitFileReceiving sent'
    currentSession['rotalResend'] = 0
    currentSessions[xferId] = eval(str(currentSession))
    sendStatus(xferId)
#    log(str(currentSessions[xferId].copy()))
    return


def handleInitFileReceiving(xferId,pktData):
    if xferId in currentSessions:
        log("Session "+xferId+" already exists")
        sendStatus(xferId, "Session "+xferId+" already exists")
        return
    
    try:
        recipientFilename,fileSize,cliHost,cliReturnPort = pktData.split('\n', 3)
    except Exception as e:
        log("Error parsing packet")
        log(e)
        return

    if cliHost == '127.0.0.1':
        cliHost = sourceIp

    currentSession = {  'type'              : 'receive',                 \
                        'recipientFilename' : str(recipientFilename),    \
                        'fileSize'          : str(fileSize),             \
                        'senderAddr'        : str(sourceIp),             \
                        'clientAddr'        : str(cliHost),              \
                        'clientPort'        : str(cliReturnPort),        \
                        'status'            : 'Recipient initializing'   }
    
    try:
        targetFile = open(recipientFilename+'.partial',"wb")
        targetFile.seek(int(fileSize)-2)
        targetFile.write(b"\0")
        targetFile.close()
    except Exception as e:
        log("Error creating target file "+recipientFilename)
        log(e)
        traceback.print_exc()
        currentSessions[xferId] = eval(str(currentSession))
        sendStatus(xferId,"Error creating target file "+recipientFilename)
        return

    # Spawn child process to handle incoming packets
    global q
    q[xferId] = mp.Queue()
    try:
        p[xferId] = multiprocessing.Process(target=runReceiverChildProcess, args=(xferId,))
        p[xferId].start()
    except OSError:
        currentSession['status'] = 'Failed to launch receiver child process'
        currentSessions[xferId] = eval(str(currentSession))
        sendStatus(xferId)
        log(str(currentSession))

    # Send copy start request to source machine
    packetData = "ucpSendFile\n"+xferId+"\n "
    sendPacket(sourceIp,listenPort,packetData)

    currentSession['status'] = 'ucpSendFile sent'
    currentSessions[xferId] = eval(str(currentSession))
    sendStatus(xferId)
    log('Receiver child launched')
    return


def handleSendFile(xferId,pktData): 
    if xferId not in currentSessions:
        log("Session "+xferId+" does not exist")
        sendStatus(xferId, "Session "+xferId+" does not exist")
        return

    currentSession = eval(str(currentSessions[xferId]))
    try:
        p[xferId] = multiprocessing.Process(target=runSenderChildProcess, args=(xferId,))
        p[xferId].start()
    except OSError:
        currentSession['status'] = 'Failed to launch sender child process'
        currentSessions[xferId] = eval(str(currentSession))
        sendStatus(xferId)
        log(str(currentSession))

    currentSessions[xferId] = eval(str(currentSession))
    log('Sender child launched')
    return
      

def handleResendFilePackets(xferId,pktData):     
    if xferId not in currentSessions:
        log("Session "+xferId+" does not exist")
        sendStatus(xferId, "Session "+xferId+" does not exist")
        return

    if 'gevald' in currentSessions[xferId] and currentSessions[xferId]['gevald'] == "1":
        log('Not servicing packet resends during Gevald period')
        return


    try:
        gapStart,gapEnd = pktData.split('\n', 1)
    except Exception as e:
        log("Error parsing packet")
        log(e)
        return

    # Send missing segment
    currentSession = eval(str(currentSessions[xferId]))
    fr=open(currentSession['fileName'],"rb")
    sr = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    addr = (currentSession['recipient'],listenPort)
    fr.seek(int(gapStart)*fileBlockSize)
    for block in range(int(gapStart),int(gapEnd)+1):
#        log(block)
        if block%senderCheckInterval == 0:
            time.sleep(float(currentSession['sendDelay']))
        if 'gevald' in currentSession and currentSession['gevald'] == "1":
            log("Gevald during resend, aborting current task")
            fr.close()
            sr.close()
            return
        data = fr.read(fileBlockSize)
        packetHash = hashlib.sha256(data).hexdigest()
        packetData = "ucpReceiveFilePacket\n"+xferId+"\n"+str(block)+"\n"+packetHash+"\n"+data
        sr.sendto(packetData,addr)
        currentSession['rotalResend'] += 1
    fr.close()
    sr.close()
    log('Re-sent blocks '+str(gapStart)+' - '+str(gapEnd)+'    Total: '+str(currentSession['rotalResend']))
    currentSessions[xferId] = eval(str(currentSession))
    sendStatus(xferId)
    return


def handleReceiveFilePacket(xferId,pktData):  
    if xferId not in currentSessions:
        log("Session "+xferId+" does not exist")
        sendStatus(xferId, "Session "+xferId+" does not exist")
        return

    q[xferId].put(pktData)
    return


def handleReceiveFileHash(xferId,pktData):  
    if xferId not in currentSessions:
        log("Session "+xferId+" does not exist")
        sendStatus(xferId, "Session "+xferId+" does not exist")
        return

    currentSession = eval(str(currentSessions[xferId]))
    currentSession['receivedFileHash'] = str(pktData)
    if 'calculatedFileHash' in currentSession and currentSession['status'] != 'File copied OK':   # Hash already calculated by child
        if currentSession['receivedFileHash'] == currentSession['calculatedFileHash']:
            currentSession['status'] = 'File copied OK'
            debug('Rename at handleReceiveFileHash: '+currentSession['recipientFilename'])
            os.rename(currentSession['recipientFilename']+'.partial', currentSession['recipientFilename'])
        else:
            currentSession['status'] = 'File hash mismatch, Foo the author'
    else:                                               # Hash not reeived yet
        currentSession['status'] = 'File hash received'

    currentSessions[xferId] = eval(str(currentSession))
    sendStatus(xferId)
    return


def handleResendFileHash(xferId,pktData):
    if xferId not in currentSessions:
        log("Session "+xferId+" does not exist")
        sendStatus(xferId, "Session "+xferId+" does not exist")
        return

    currentSession = eval(str(currentSessions[xferId]))
    if 'fileHash' not in currentSession:
        currentSession['fileHash'] = calculateFileHash(xferId,fileName)
        currentSessions[xferId] = eval(str(currentSession))

    fileHash = currentSession['fileHash'] 
    packetData = "ucpReceiveFileHash\n"+xferId+"\n"+currentSession['fileHash']
    sendPacket(currentSession['recipient'],listenPort,packetData)
    log('file hash re-sent')
    return


def handleAckFile(xferId,pktData): 
    if xferId not in currentSessions:
        log("Session "+xferId+" does not exist")
        sendStatus(xferId, "Session "+xferId+" does not exist")
        return
    
    log('File acknowledged, deleting session for '+xferId)
    del currentSessions[xferId]
    return


def handleRequestStatus(xferId,pktData):  
    if xferId not in currentSessions:
        log("Session "+xferId+" does not exist")
        sendStatus(xferId, "Session "+xferId+" does not exist")
        return

    currentSession = eval(str(currentSessions[xferId]))
    sendStatus(xferId)

    if currentSession['status'] == 'Waiting for hash':
        packetData = "ucpResendFileHash\n"+xferId+"\n "
        sendPacket(currentSession['senderAddr'],listenPort,packetData)
        log('Requested resending file hash in handleRequestStatus')
        return
    return


def handleGevald(xferId,pktData):
    if xferId not in currentSessions:
        log("Session "+xferId+" does not exist")
        sendStatus(xferId, "Session "+xferId+" does not exist")
        return

    currentSession = eval(str(currentSessions[xferId]))
    currentSession['gevald'] = "1"
    currentSessions[xferId] = eval(str(currentSession))
    return


def handleUnGevald(xferId,pktData):
    if xferId not in currentSessions:
        log("Session "+xferId+" does not exist")
        sendStatus(xferId, "Session "+xferId+" does not exist")
        return

    currentSession = eval(str(currentSessions[xferId]))
    currentSession['gevald'] = "0"
    currentSessions[xferId] = eval(str(currentSession))
    return


def handleSpeedUp(xferId,pktData):
    if xferId not in currentSessions:
        log("Session "+xferId+" does not exist")
        sendStatus(xferId, "Session "+xferId+" does not exist")
        return

    currentSession = eval(str(currentSessions[xferId]))
    currDelay = float(currentSession['sendDelay']) 
    if currDelay > 0:
        currDelay -= 0.001
        currentSession['sendDelay'] = str(currDelay)
        currentSessions[xferId] = eval(str(currentSession))
        log('Reducing delay to '+str(currDelay))
    return


def handleSlowDown(xferId,pktData):
    if xferId not in currentSessions:
        log("Session "+xferId+" does not exist")
        sendStatus(xferId, "Session "+xferId+" does not exist")
        return

    currentSession = eval(str(currentSessions[xferId]))
    currDelay = float(currentSession['sendDelay']) 
    currDelay += 0.001
    currentSession['sendDelay'] = str(currDelay)
    currentSessions[xferId] = eval(str(currentSession))
    log('Increasing delay to '+str(currDelay))
    return


def handleAbort(xferId,pktData):
    if xferId not in currentSessions:
        log("Session "+xferId+" does not exist")
        sendStatus(xferId, "Session "+xferId+" does not exist")
        return
    
    if xferId in p:
        log('Got abort request, terminating child process for session '+xferId)
        p[xferId].terminate()
        p[xferId].join()
        
    log('Deleting session for '+xferId)
    del currentSessions[xferId]
    return


def handleUnknown(xferId,pktData):
    log("Unknown packet type")
    return


def handlePacket(pktType,xferId,pktData):
    pktTypes={
        'ucpInitFileSending':         handleInitFileSending,      
        'ucpInitFileReceiving':       handleInitFileReceiving,
        'ucpSendFile':                handleSendFile,
        'ucpResendFilePackets':       handleResendFilePackets,     
        'ucpReceiveFilePacket':       handleReceiveFilePacket,
        'ucpReceiveFileHash':         handleReceiveFileHash,
        'ucpResendFileHash':          handleResendFileHash,
        'ucpAckFile':                 handleAckFile,
        'ucpRequestStatus':           handleRequestStatus,  
        'ucpGevald':                  handleGevald,
        'ucpUnGevald':                handleUnGevald,
        'ucpSpeedUp':                 handleSpeedUp,
        'ucpSlowDown':                handleSlowDown,
        'ucpAbort':                   handleAbort
        }
    action=pktTypes.get(pktType,handleUnknown)
    return action(xferId,pktData)



# Main - execution starts here
r = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
try:
    r.bind((listenHost,listenPort+myPortOffset))
except Exception as e:
    log("Error binding to UDP socket on port "+str(listenPort))
    log(e)
    exit(1)

r.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,500000) 
bufsize = r.getsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF) 
log("Buffer size: %d" %bufsize) 
log("Listen port: "+str(listenPort+myPortOffset))
udpBufferHighWatermark = int(bufsize*0.8)
udpBufferLowWatermark = int(bufsize*0.6)
udpBufferHouseKeepingWatermark = int(bufsize*0.1)

while(True):
    try:
        data,sourceIpPort = r.recvfrom(udpMaxPacketSize)
    except socket.timeout:
        log("timeout")
    except Exception as e:
        log("Error getting packet "+pktType)
        log(e)


    pktType,xferIdHex,pktData = data.split('\n', 2)
    sourceIp = sourceIpPort[0]

#    if sourceIp == '10.0.2.2':
#        sourceIp = '127.0.0.1'
#        log("**** DEV DEV DEV  Source IP changed from 10.0.2.2 to 127.0.0.1 *****")

#    log("")
#    log("Received from: "+sourceIp)
#    log("Message Type: "+pktType)
#    log("Transfer ID: "+xferIdHex)
    try:
        handlePacket(pktType,str(xferIdHex),pktData)
    except Exception as e:
        import pdb
        log("Error handling packet "+pktType)
        log(e)
        traceback.print_exc()
        pdb.post_mortem()
