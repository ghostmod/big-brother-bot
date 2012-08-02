# Changelog
# 2010/07/23 - xlr8or - v1.0.1
# * fixed infinite loop in a python socket thread in receivePacket() on gameserver restart

__version__ = '1.0.1'

import logging
import time
import asyncore
import socket
import threading
import binascii
import string
import select
import Queue

from collections import deque




#####################################################################################
class BattleyeError(Exception): pass

class CommandError(BattleyeError): pass
class CommandTimeoutError(CommandError): pass
class CommandFailedError(CommandError): pass

class NetworkError(BattleyeError): pass

class BattleyeServer(threading.Thread):

    def __init__(self, host, port, password):
        threading.Thread.__init__(self, name="BattleyeServerThread")
        #self.logger = logging.getLogger(__name__)
        #hdlr = logging.FileHandler('G:/b3-182/arma/logs/battleye.log')
        #formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
        #hdlr.setFormatter(formatter)
        #self.logger.addHandler(hdlr)
        #self.logger.setLevel(logging.DEBUG)
        self.getLogger().debug("connecting")
        self._isconnected = False
        self._isrunning = True
        self.server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.server.connect((host, port))
        self.password = password
        self.read_queue = deque([])
        self.write_queue = deque([])
        self.say_queue = deque([])
        self.command_queue = deque([])
        self.server_response_queue = deque ([])
        self.server_message_queue = deque ([])
        self.sent_data_seq = []
        self.observers = set()
        self._stopEvent = threading.Event()
        self.server_thread = threading.Thread(target=self.polling_thread)
        self.server_thread.setDaemon(True)
        self.server_thread.start()
        self.getLogger().debug("Battleye server thread")        
        
        self.start()
        time.sleep(1.5)
        #self.start_listening()



    def polling_thread(self):
        """Starts a thread for reading/writing to the Battleye server."""
        server = self.server
        self.getLogger().debug("Starting server thread %s" % server)
        self._isrunning = True
        while self._isrunning:
            #self.getLogger().debug("Is socket ready")
            readable, writable, exception = select.select([server],[server],[server], .1)
            
            if not exception:
                if readable:
                    try:
                        data, addr = server.recvfrom(8192)
                        self.read_queue.append(data)
                        self.getLogger().debug("Read data: %s" % repr(data))
                    except socket.error, (value,message): 
                        self.getLogger().debug("Socket error %s %s" % (value, message))
                        self._isrunning = False
                if writable:
                    if len(self.write_queue):
                        data = self.write_queue.popleft()
                        self.getLogger().debug("Data to send: %s" % repr(data))
                        try:
                            server.send(data)
                        except:
                            self.getLogger().debug("Data send error, trying again")
                            self.write_queue.appendleft(data)
                        else:
                            #store seq_no, type, data
                            if data[7:8] == chr(1):
                                seq = ord(data[8:9])
                                self.getLogger().debug("Sent sequence was %s" % seq)
                                self.sent_data_seq.append(seq)
                        
            else:
                self._isrunning = False
            
            
        self.getLogger().debug("Ending Polling Thread")

    def run(self):
        crc_error_count = 0
        self.getLogger().debug("Start Listening")
        self._isconnected = self.login()
        write_seq = 0
        last_write_time = time.time()
        while self._isconnected and not self.isStopped() and self._isrunning:
            if len(self.read_queue):
                packet = self.read_queue.popleft()
                type, sequence, data = self.decode_server_packet(packet)
                if type == chr(2):
                    # Acknowledge server message receipt
                    request = self.encode_packet(chr(2), sequence)
                    self.write_queue.append(request)
                    last_write_time = time.time()
                    self._on_event( (2, data) )
                elif type == chr(1):
                    self.getLogger().debug('Command Response : %s' % repr(data))
                    try:
                        self.sent_data_seq.remove(ord(sequence))
                    except KeyError:
                        pass
                    crc_error_count = 0
                    if data:
                        self._on_event( (1, data) )
                elif type == chr(255):
                    #CRC Error
                    crc_error_count += 1
                        
            if len(self.command_queue):
                request = self.encode_packet(chr(1), chr(write_seq) + self.command_queue.popleft())
                self.write_queue.append(request)
                last_write_time = time.time()
                write_seq += 1
            elif len(self.say_queue):
                request = self.encode_packet(chr(1), chr(write_seq) + self.say_queue.popleft())
                self.write_queue.append(request)
                last_write_time = time.time()
                write_seq += 1
            elif last_write_time + 30 < time.time():
                request =  self.encode_packet(chr(1), chr(write_seq))
                self.write_queue.append(request)
                last_write_time = time.time()
                write_seq += 1
                
            if crc_error_count > 10 or len(self.sent_data_seq) > 10:
                # 10 + consecutive crc errors or 10 commands not replied to
                self.stop()
                
        self.getLogger().debug("Ending Server Thread")
        self._isconnected = False
                    
    def login(self):
        """authenticate on the Battleye server with given password"""
        self.getLogger().debug("Starting Login")
        #crc1, crc2, crc3, crc4 = self.compute_crc(chr(255) + chr(0) + self.password)
        #request = "BE" + chr(crc1) + chr(crc2) + chr(crc3) + chr(crc4) + '\xff' + chr(0) + self.password
        request =  self.encode_packet(chr(0), self.password)
        self.getLogger().debug(self.write_queue)
        self.write_queue.append(request)
        self.getLogger().debug(self.write_queue)
        login_response = False
        t = time.time()
        while time.time() < t+3 and not login_response:
            if len(self.read_queue):
                packet = self.read_queue.popleft()
                type, logged_in, data =  self.decode_server_packet(packet)
                self.getLogger().debug("login response was %s %s %s" % (type, logged_in, data))
                if type == chr(255):
                    self.getLogger().debug('Invalid packet')
                elif type == chr(0):
                    login_response = True

        if login_response:
            if logged_in == chr(1):
                self.getLogger().debug("Login Successful")
                return True
        else:
            self.getLogger().debug("Login Failed")
            return False

    def command(self, cmd):
        self.say_queue.append(cmd)

    def compute_crc(self, data):
        crc = binascii.crc32(data) & 0xffffffff
        crc32 = '0x%08x' % crc
        # self.getLogger().debug("crc32 = %s" % crc)
        return int(crc32[8:10], 16), int(crc32[6:8], 16), int(crc32[4:6], 16), int(crc32[2:4], 16)

    def decode_server_packet(self, packet):
        if packet[0:2] != 'BE':
            return chr(255), '', ''

        packet_crc = packet[2:6]
        self.getLogger().debug("Packet crc: %s" % repr(packet_crc))
        crc1, crc2, crc3, crc4 =  self.compute_crc(packet[6:])
        computed_crc = chr(crc1) + chr(crc2) + chr(crc3) + chr(crc4)
        # self.getLogger().debug("Computed crc: %s" % repr(computed_crc))
        if packet_crc != computed_crc:
            self.getLogger().debug('Invalid crc')
            return chr(255), '', ''

        type = packet[7:8]
        sequence_no = packet[8:9]
        data = packet[9:]
        return type, sequence_no, data

    def encode_packet(self, packet_type, data):
        data_to_send = bytearray()
        try:
            if data and isinstance(data, str):
                data=unicode(data, errors='ignore')
            data=data.encode('Latin-1', 'replace')
        except Exception, msg:
            self.getLogger().debug('ERROR encoding data: %r' % msg)
            data = 'Encoding error'
        self.getLogger().debug('Encoded data is %s' % data)
        data_to_send = data_to_send + chr(255) + packet_type + bytearray(data, 'Latin-1', 'ignore')
        crc1, crc2, crc3, crc4 = self.compute_crc(data_to_send)
        request =  "B" + "E" + chr(crc1) + chr(crc2) + chr(crc3) + chr(crc4) + data_to_send
        self.getLogger().debug("Request is type : %s" % type(request))
        return request
        
    def __getattr__(self, name):
        if name == 'connected':
            return self._isconnected
        else:
            self.name
            

    def subscribe(self, func):
        """Add func from Battleye events listeners."""
        self.observers.add(func)
        
    def unsubscribe(self, func):
        """Remove func from Battleye events listeners."""
        self.observers.remove(func)

        
    def _on_event(self, words):
        self.getLogger().debug("received Battleye event : %s" % repr(words))
        for func in self.observers:
            func(words)
            
            

    def stop(self):
        self.getLogger().debug("stopping Threads...")
        self._isconnected = False
        self._isrunning = False
        self._stopEvent.set()
        

    def isStopped(self):
        return self._stopEvent.is_set()

    def getLogger(self):
        return logging.getLogger("BattleyeServer")