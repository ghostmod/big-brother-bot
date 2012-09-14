# Changelog


__version__ = '1.0.2'

import logging
import time
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
        self.read_queue = Queue.Queue([])
        self.write_queue = deque([])
        self.say_queue = Queue.Queue([])
        self.server_response_queue = deque ([])
        self.server_message_queue = deque ([])
        self.sent_data_seq = []
        self.observers = set()
        self._polling_delay = 0.05
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
                        self.read_queue.put(data)
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
                        except Exception, err:
                            self.getLogger().debug("Data send error, trying again. %s" % err, exc_info=err)
                            self.write_queue.appendleft(data)
                        else:
                            #store seq_no, type, data
                            if data[7:8] == chr(1):
                                seq = ord(data[8:9])
                                self.getLogger().debug("Sent sequence was %s" % seq)
                                self.sent_data_seq.append(seq)

                time.sleep(self._polling_delay)
                        
            else:
                self._isrunning = False
            
        self.getLogger().debug("Ending Polling Thread")
        self.stop()
        
        
    def run(self):
        request = bytearray()
        packet = bytearray()
        self.crc_error_count = 0
        self.getLogger().debug("Start Listening")
        self._isconnected = self.login()

        if self._isconnected:
            self.read_thread = threading.Thread(target=self.reading_thread)
            self.read_thread.setDaemon(True)
            self.read_thread.start()
            self.write_thread = threading.Thread(target=self.writing_thread)
            self.write_thread.setDaemon(True)
            self.write_thread.start()
        while self._isconnected and not self.isStopped():
            if self.crc_error_count > 10 or len(self.sent_data_seq) > 10:
                self.getLogger().debug('CRC Errors %s   Commands not replied to %s' % (self.crc_error_count, self.sent_data_seq))
                # 10 + consecutive crc errors or 10 commands not replied to
                self.stop()
                
            time.sleep(10)

            #time.sleep(self._process_delay)
                
        self.getLogger().debug("Ending Server Thread")
        
        
    def reading_thread(self):
        self.getLogger().debug("Starting Reading Thread")
        while self._isconnected and not self.isStopped():
            try:
                packet = self.read_queue.get(timeout = 2)
                type, sequence, data = self.decode_server_packet(packet)
                if type == 2:
                    # Acknowledge server message receipt
                    packet = self.encode_packet(2, sequence, None)
                    self.getLogger().debug("Server Message sequence was %s" % sequence)
                    self.write_queue.append(packet)
                    last_write_time = time.time()
                    self._on_event( (2, data) )
                elif type == 1:
                    #self.getLogger().debug('Command Response : %s' % repr(data))
                    try:
                        self.sent_data_seq.remove(sequence)
                    except ValueError:
                        pass
                    crc_error_count = 0
                    if data:
                        self._on_event( (1, data) )
                elif type == 255:
                    #CRC Error
                    self.crc_error_count += 1
                
            except Queue.Empty:
                pass
            except Exception, err:
                self.getLogger().error("error in reading_thread", exc_info=err)
                
        self.getLogger().debug("Ending Reading Thread")
                
    def writing_thread(self):
        self.getLogger().debug("Starting Writing Thread")
        write_seq = 0
        last_write_time = time.time()
        while self._isconnected and not self.isStopped():
            try:
                request = self.say_queue.get(timeout = 2)
                packet = self.encode_packet(1, write_seq, request)
                last_write_time = time.time()
                self.write_queue.append(packet)
                write_seq += 1
                if write_seq > 255:
                    write_seq -= 256
                
            except Queue.Empty:
                if last_write_time + 30 < time.time():
                    last_write_time = time.time()
                    packet =  self.encode_packet(1, write_seq, None)
                    self.write_queue.append(packet)
                    write_seq += 1
                    if write_seq > 255:
                        write_seq -= 256
            except Exception, err:
                self.getLogger().error("error in writing_thread", exc_info=err)
                
        self.getLogger().debug("Ending Writing Thread")

                    
    def login(self):
        """authenticate on the Battleye server with given password"""
        self.getLogger().debug("Starting Login")
        request =  self.encode_packet(0, None, self.password)
        self.getLogger().debug(self.write_queue)
        self.write_queue.append(request)
        self.getLogger().debug(self.write_queue)
        login_response = False
        t = time.time()
        logged_in = None
        while time.time() < t+3 and not login_response:
            try:
                packet = self.read_queue.get(timeout = 0.1)
                type, logged_in, data =  self.decode_server_packet(packet)
                self.getLogger().debug("login response was %s %s %s" % (type, logged_in, data))
                if type == chr(255):
                    self.getLogger().debug('Invalid packet')
                elif type == 0:
                    login_response = True
            except Queue.Empty:
                pass

        if login_response:
            if logged_in == 1:
                self.getLogger().debug("Login Successful")
                return True
        else:
            self.getLogger().debug("Login Failed")
            return False

    def command(self, cmd):
        self.say_queue.put(cmd)

    def compute_crc(self, data):
        buf = buffer(data)
        crc = binascii.crc32(buf) & 0xffffffff
        crc32 = '0x%08x' % crc
        # self.getLogger().debug("crc32 = %s" % crc)
        return int(crc32[8:10], 16), int(crc32[6:8], 16), int(crc32[4:6], 16), int(crc32[2:4], 16)

    def decode_server_packet(self, packet):
        if packet[0:2] != b'BE':
            return 255, '', ''

        packet_crc = packet[2:6]
        #self.getLogger().debug("Packet crc: %s" % repr(packet_crc))
        crc1, crc2, crc3, crc4 =  self.compute_crc(packet[6:])
        computed_crc = chr(crc1) + chr(crc2) + chr(crc3) + chr(crc4)
        # self.getLogger().debug("Computed crc: %s" % repr(computed_crc))
        if packet_crc != computed_crc:
            self.getLogger().debug('Invalid crc')
            return 255, '', ''

        type = ord(packet[7:8])
        sequence_no = ord(packet[8:9])
        data = packet[9:]
        return type, sequence_no, data

    def encode_packet(self, packet_type, seq, data):
        data_to_send = bytearray()

        #self.getLogger().debug('Encoded data is %s' % data)
        #data_to_send = data_to_send + chr(255) + packet_type + bytearray(data, 'Latin-1', 'ignore')
        data_to_send.append(255)
        data_to_send.append(packet_type)
        if seq is not None:
            data_to_send.append(seq)
        if data:
            try:
                if data and isinstance(data, str):
                    data=unicode(data, errors='ignore')
                data=data.encode('UTF-8', 'replace')
            except Exception, msg:
                self.getLogger().debug('ERROR encoding data: %r' % msg)
                data = 'Encoding error'
                
            data_to_send.extend(data)
        crc1, crc2, crc3, crc4 = self.compute_crc(data_to_send)
        # request =  "B" + "E" + chr(crc1) + chr(crc2) + chr(crc3) + chr(crc4) + data_to_send
        request = bytearray(b'BE')
        request.append(crc1)
        request.append(crc2)
        request.append(crc3)
        request.append(crc4)
        request.extend(data_to_send)
        #self.getLogger().debug("Request is type : %s" % type(request))
        return request
        
    def __getattr__(self, name):
        if name == 'connected':
            return self._isconnected
        else:
            return self.name
            

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