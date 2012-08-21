#
# BigBrotherBot(B3) (www.bigbrotherbot.net)
# Copyright (C) 2011 Thomas LEVEIL
# 
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
#
# CHANGELOG
# 17/28/2012    0.1     Initial release
#  

#

__author__  = 'Courgette, 82ndab-Bravo17'
__version__ = '0.1'


import sys, re, traceback, time, string, Queue, threading, new
import b3.parser
from b3.parsers.battleye.rcon import Rcon as BattleyeRcon
from b3.parsers.battleye.protocol import BattleyeServer, CommandFailedError, CommandError, NetworkError
import b3.events
import b3.cvar
from b3.functions import getStuffSoundingLike


# how long should the bot try to connect to the Battleye server before giving out (in second)
GAMESERVER_CONNECTION_WAIT_TIMEOUT = 600

class AbstractParser(b3.parser.Parser):
    """
    An base class to help with developing battleye parsers
    """

    gameName = None

    # hard limit for rcon command admin.say
    SAY_LINE_MAX_LENGTH = 128

    OutputClass = BattleyeRcon
    _serverConnection = None
    _nbConsecutiveConnFailure = 0

    battleye_event_queue = Queue.Queue(400)
    sayqueue = Queue.Queue(100)
    sayqueuelistener = None

    # battleye engine does not support color code, so we need this property
    # in order to get stripColors working
    _reColor = re.compile(r'(\^[0-9])')

    _settings = {
        'line_length': 128,
        'min_wrap_length': 128,
        'message_delay': .8,
        'big_msg_duration': 4,
        'big_b3_private_responses': False,
        }

    _gameServerVars = () # list available cvar

    _commands = {
        'message': ('say', '%(cid)s', '%(message)s'),
        'say': ('say -1' , '%(message)s'),
        'kick': ('kick', '%(cid)s', '%(reason)s'),
        'ban': ('ban', '%(cid)s', '0', '%(reason)s'),
        'banByGUID': ('addBan', '%(guid)s', '0', '%(reason)s'),
        'unban': ('removeBan', '%(ban_no)s'),
        'tempban': ('ban', '%(cid)s', '%(duration)d', '%(reason)s'),
        'tempbanByGUID': ('addBan', '%(guid)s', '%(duration)d', '%(reason)s'),
        'shutdown': ('#shutdown', ),
        }

    _eventMap = {
    }

    _client_auth_waiting = {}
    _player_list_from_server = ''
    _previous_player_list_from_server = ''
    _ban_list_from_server = {}
    _multi_packet_response = {}
    _server_response_lock = False
    _server_response_timeout = 5
    _player_list_errors = 0
    _regPlayer = re.compile(r'^(?P<cid>[0-9]+)\s+(?P<ip>[0-9.]+):(?P<port>[0-9]+)\s+(?P<ping>[0-9-]+)\s+(?P<guid>[0-9a-f]+)\((?P<verified>[A-Z]+)\)\s+(?P<name>.*?)$', re.I)
    _regPlayer_lobby = re.compile(r'^(?P<cid>[0-9]+)\s+(?P<ip>[0-9.]+):(?P<port>[0-9]+)\s+(?P<ping>[0-9-]+)\s+(?P<guid>[0-9a-f]+)\((?P<verified>[A-Z]+)\)\s+(?P<name>.*?)(?P<lobby>\(Lobby\))$', re.I)
    
    # if ban_with_server is True, then the Battleye server will be used for ban
    ban_with_server = True

    # flag to find out if we need to fire a EVT_GAME_ROUND_START event.
    _waiting_for_round_start = True

    def run(self):
        """Main worker thread for B3"""
        for t in threading.enumerate():
            self.debug('Still running %s', t.getName())
        self.bot('Start listening ...')
        self.screen.write('Startup Complete : B3 is running! Let\'s get to work!\n\n')
        self.screen.write('(If you run into problems, check %s for detailed log info)\n' % self.config.getpath('b3', 'logfile'))
        #self.screen.flush()

        self.updateDocumentation()
        server_logging = self.load_protocol_logging()
        ## the block below can activate additional logging for the BattleyeServer class
        if server_logging:
            import logging
            from b3.output import OutputHandler
            battleyeServerLogger = logging.getLogger("BattleyeServer")
            for handler in logging.getLogger('output').handlers:
                battleyeServerLogger.addHandler(handler)
            battleyeServerLogger.setLevel(logging.getLogger('output').level)
        
            ## This block will send the logging info to a separate file
            FORMAT = "%(asctime)s %(name)-20s [%(thread)-4d] %(threadName)-15s %(levelname)-8s %(message)s"
            #FORMAT = '%(asctime)s %(levelname)s %(message)s'
         
            hdlr = logging.FileHandler(server_logging)
            formatter = logging.Formatter(FORMAT)
            hdlr.setFormatter(formatter)
            battleyeServerLogger.addHandler(hdlr)
            battleyeServerLogger.setLevel(logging.DEBUG)

        while self.working:
            if not self._serverConnection or not self._serverConnection.connected:
                try:
                    self.setup_battleye_connection()
                except IOError, err:
                    self.error("IOError %s"% err)
                except Exception, err:
                    self.error(err)
                    self.exitcode = 220
                    break

            try:
                added, expire, event = self.battleye_event_queue.get(timeout=5)
                type = event[0]
                packet = event[1]
                if type == 1:
                    self.routeBattleyeResponsePacket(packet)
                if type ==2:
                    self.routeBattleyeMessagePacket(packet)
            except Queue.Empty:
                self.verbose2("no game server event to treat in the last 5s")
            except CommandError, err:
                # it does not matter from the parser perspective if Battleye command failed
                # (timeout or bad reply)
                self.warning(err)
            except NetworkError, e:
                # the connection to the battleye server is lost
                self.warning(e)
                self.close_battleye_connection()
            except Exception, e:
                self.error("unexpected error, please report this on the B3 forums")
                self.error(e)
                # unexpected exception, better close the battleye connection
                self.close_battleye_connection()

        #self._serverConnection.stop()
        self.close_battleye_connection()
        self.info("Stop listening for Battleye events")

        # exiting B3
        with self.exiting:
            # If !die or !restart was called, then  we have the lock only after parser.handleevent Thread releases it
            # and set self.working = False and this is one way to get this code is executed.
            # Else there was an unhandled exception above and we end up here. We get the lock instantly.

            self.output.battleye_server = None

            # The Battleye connection is running its own thread to communicate with the game server. We need to tell
            # this thread to stop.
            # self.close_battleye_connection()

            # If !die was called, exitcode have been set to 222
            # If !restart was called, exitcode have been set to 221
            # In both cases, the SystemExit exception that triggered exitcode to be filled with an exit value was
            # caught. Now that we are sure that everything was gracefully stopped, we can re-raise the SystemExit
            # exception.
            if self.exitcode:
                sys.exit(self.exitcode)

    def setup_battleye_connection(self):
        '''
        Setup the Connection to the Battleye server
        '''
        self.info('Connecting to battleye server  on %s:%s...' % (self._rconIp, self._rconPort))
        if self._serverConnection:
            self.close_battleye_connection()

        self._serverConnection = BattleyeServer(self._rconIp, self._rconPort, self._rconPassword)
        self.info("Server Connection is %s" % repr(self._serverConnection))
        timeout = GAMESERVER_CONNECTION_WAIT_TIMEOUT + time.time()
        while time.time() < timeout and not self._serverConnection.connected:
            self.info("retrying to connect to game server...")
            time.sleep(2)
            self.close_battleye_connection()
            self._serverConnection = BattleyeServer(self._rconIp, self._rconPort, self._rconPassword)

        if self._serverConnection is None or not self._serverConnection.connected:
            self.error("Could not connect to Battleye server")
            self.close_battleye_connection()
            self.shutdown()
            raise SystemExit()

        # listen for incoming game server events
        self._serverConnection.subscribe(self.OnBattleyeEvent)

        # self._serverConnection.auth()
        # self._serverConnection.command('admin.eventsEnabled', 'true')

        # setup Rcon
        self.output.set_battleye_server(self._serverConnection)

        self.queueEvent(b3.events.Event(b3.events.EVT_GAMESERVER_CONNECT, None))

        self.say('%s ^2[ONLINE]' % b3.version)
        #self.getServerInfo()
        #self.getServerVars()
        #self.clients.sync()


    def close_battleye_connection(self):
        #try:
        self._serverConnection.stop()
        #except Exception:
        #    pass
        self._serverConnection = None
        time.sleep(5)

    def OnBattleyeEvent(self, packet):
        if not self.working:
            try:
                self.verbose("dropping Battleye event %r" % packet)
            except:
                pass
        self.console(repr(packet))
        try:
            self.battleye_event_queue.put((self.time(), self.time() + 10, packet), timeout=2)
            self.info('Battleye Event Queue: %s' % repr(self.battleye_event_queue))
        except Queue.Full:
            self.error("Battleye event queue full, dropping event %r" % packet)

            
#'RCon admin #0 (76.108.91.78:62382) logged in'
#'Player #0 Bravo17 (76.108.91.78:2304) connected'
#'Player #0 Bravo17 - GUID: 80a5885ebe2420bab5e158a310fcbc7d (unverified)'
#'Verified GUID (80a5885ebe2420bab5e158a310fcbc7d) of player #0 Bravo17'
#'Player #2 NZ (04b81a0bd914e7ba610ef3c0ffd66a1a) has been kicked by BattlEye: Script Restriction #107'
#'Player #4 Kauldron disconnected'
#(Lobby) Bravo17: hello b3'
#(Global) Bravo17: global channel
#Players on server:\n
#[#] [IP Address]:[Port] [Ping] [GUID] [Name]\n
#--------------------------------------------------\n
#0   76.108.91.78:2304     63   80a5885ebe2420bab5e158a310fcbc7d(OK) Bravo17\n
#(1 players in total)'

    
#Players on server:\n
#[#] [IP Address]:[Port] [Ping] [GUID] [Name]\n--------------------------------------------------\n
#0   76.108.91.78:2304     63   80a5885ebe2420bab5e158a310fcbc7d(OK) Bravo17\n
#(1 players in total)'
    


    def routeBattleyeMessagePacket(self, packet):
        '''
        Decide what to do with the packet received from the Battleye server
        '''
        
        if packet is None:
            self.warning('cannot route empty packet : %s' % traceback.extract_tb(sys.exc_info()[2]))
            
        self.info('Server Packet is %s' % packet)
        eventData = ''
        eventType = ''
        if packet.startswith('RCon admin #'):
            func = 'OnServerMessage'
            eventData = packet[12:]
        elif packet[0:8] == 'Player #':
            if packet[-9:] == 'connected':
                func ='OnPlayerConnected'
                eventData = packet[8:len(packet)-10]
            elif packet[-12:] == 'disconnected':
                func ='OnPlayerLeave'
                eventData = packet[8:len(packet)-13]
            elif packet[-12:] == '(unverified)':
                func = 'OnUnverifiedGUID'
                eventData = packet[8:len(packet)-13]
            elif packet.find(' has been kicked by BattlEye: '):
                func = 'OnBattleyeKick'
                eventData = packet[8:]
            else:
                self.debug('Unhandled server message %s' % packet)
                eventData = None
                func = 'OnUnknownEvent'
        elif packet[0:13] == 'Verified GUID':
            func = 'OnVerifiedGUID'
            eventData = (packet[15:])
        elif packet[0:7] == '(Lobby)':
            func = 'OnPlayerChat'
            eventData = packet[7:] + ' (Lobby)'
        elif packet[0:8] == '(Global)':
            func = 'OnPlayerChat'
            eventData = packet[8:] + ' (Global)'
        elif packet[0:8] == '(Direct)':
            func = 'OnPlayerChat'
            eventData = packet[8:] + ' (Direct)'
        elif packet[0:9] == '(Vehicle)':
            func = 'OnPlayerChat'
            eventData = packet[9:] + ' (Vehicle)'
        elif packet[0:7] == '(Group)':
            func = 'OnPlayerChat'
            eventData = packet[7:] + ' (Group)'
        elif packet[0:6] == '(Side)':
            func = 'OnPlayerChat'
            eventData = packet[6:] + ' (Side)'
        elif packet[0:9] == '(Command)':
            func = 'OnPlayerChat'
            eventData = packet[9:] + ' (Command)'

        else:
            self.debug('Unhandled server message %s' % packet)
            eventData = None
            func = 'OnUnknownEvent'
        
        self.call_func(func, eventType, eventData)


            
    def call_func(self, func, eventType, eventData):
        if hasattr(self, func):
            #self.verbose2('routing ----> %s(%r)' % (func,eventData))
            func = getattr(self, func)
            event = func(eventType, eventData)
            #self.debug('event : %s' % event)
            if event:
                self.queueEvent(event)
            
        elif eventType in self._eventMap:
            self.queueEvent(b3.events.Event(
                    self._eventMap[eventType],
                    eventData))
        else:
            data = ''
            if func:
                data = func + ' '
            data += str(eventType) + ': ' + str(eventData)
            self.warning('TODO : handle \'%r\' battleye events' % packet)
            self.queueEvent(b3.events.Event(b3.events.EVT_UNKNOWN, data))

        
    def routeBattleyeResponsePacket(self, packet):
        '''
        Join together packets to get the complete server response
        '''
        self.debug('Responsepacket is %s' % packet)
        eventType = ''
        eventData = ''
        func = ''
        if packet[0:1] == chr(0):
            # we have a Multipacket response
            total_packets = ord(packet[1])
            this_packet = ord(packet[2])

            self._multi_packet_response[this_packet] = packet[3:]
            if this_packet == total_packets - 1:
                packet = ''
                for p in range(0, total_packets-1):
                    if len(self._multi_packet_response[p]):
                        packet = packet + self._multi_packet_response[p]
                    else:
                        self.debug('Part of Multi packet response is missing')
                        for pp in range(0, total_packets-1):
                            self._multi_packet_response[pp] = ''
                        return
                # Packet reconstituted, so delete segments
                for pp in range(0, total_packets-1):
                    self._multi_packet_response[pp] = ''
                
            else:
                return
                
        if packet[0:18] == 'Players on server:':
            func = 'OnPlayerList'        
            self.debug('Found playerlist')
            eventData = packet
        elif packet[0:10] == 'GUID Bans:':
            func = 'OnBanList'
            self.debug('Got Bans List')
            eventData = packet
        elif packet == 'Unknown command':
            self.debug('Server recieved an Unknown command from us')
        else:
            self.debug('Unhandled server response %s' % packet)

        if func:
            self.call_func(func, eventType, eventData)
        

    def startup(self):
       
        # add specific events
        self.Events.createEvent('EVT_GAMESERVER_CONNECT', 'connected to game server')
        self.Events.createEvent('EVT_CLIENT_SPAWN', 'Client Spawn')
        self.Events.createEvent('EVT_PLAYER_SYNC_COMPLETED', 'Players syncing finished')

        self.load_conf_max_say_line_length()
        self.load_config_message_delay()

        self.start_sayqueue_worker()

        # start crontab to trigger playerlist events
        self.cron + b3.cron.CronTab(self.clients.sync, minute='*/1')
        self.clients.newClient('Server', guid='Server', name='Server', hide=True, pbid='Server', team=b3.TEAM_UNKNOWN, squad=None)

    def pluginsStarted(self):
        # self.patch_b3_admin_plugin()
        return
        
    def sayqueuelistener_worker(self):
        self.info("sayqueuelistener job started")
        while self.working:
            try:
                msg = self.sayqueue.get(timeout=40)
                for line in self.getWrap(self.stripColors(self.msgPrefix + ' ' + msg), self._settings['line_length'], self._settings['min_wrap_length']):
                    self.write(self.getCommand('say', message=line))
                    time.sleep(self._settings['message_delay'])
            except Queue.Empty:
                self.verbose2("sayqueuelistener: had nothing to do in the last 40 sec")
            except Exception, err:
                self.info("sayqueuelistener Error")
                
        self.info("sayqueuelistener job ended")

    def start_sayqueue_worker(self):
        self.sayqueuelistener = threading.Thread(target=self.sayqueuelistener_worker)
        self.sayqueuelistener.setDaemon(True)
        self.sayqueuelistener.start()


    def getCommand(self, cmd, **kwargs):
        """Return a reference to a loaded command"""
        try:
            cmd = self._commands[cmd]
        except KeyError:
            return None

        preparedcmd = []
        for a in cmd:
            try:
                preparedcmd.append(a % kwargs)
            except KeyError:
                pass
        
        result = tuple(preparedcmd)
        self.debug('getCommand: %s', result)
        return result
    
    def write(self, msg, maxRetries=1, needConfirmation=False):
        """Write a message to Rcon/Console
        Unfortunaltely this has been abused all over B3 
        and B3 plugins to broadcast text :(
        """
        if type(msg) == str:
            # console abuse to broadcast text
            self.say(msg)
        else:
            # Then we got a command, so unpack it
            cmd = ''
            for a in msg:
                cmd = cmd + ' ' + a
                
            cmd = cmd.strip()
                
            if self.replay:
                self.bot('Sent rcon message: %s' % cmd)
            elif self.output is None:
                pass
            else:
                res = self.output.write(cmd, maxRetries=maxRetries, needConfirmation=needConfirmation)
                self.output.flush()
                return res
            
    def getWrap(self, text, length=None, minWrapLen=None):
        """Returns a sequence of lines for text that fits within the limits
        """
        if not text:
            return []

        if length is None:
            length = self._settings['line_length']

        maxLength = int(length)
        
        if len(text) <= maxLength:
            return [text]
        else:
            wrappoint = text[:maxLength].rfind(" ")
            if wrappoint == 0:
                wrappoint = maxLength
            lines = [text[:wrappoint]]
            remaining = text[wrappoint:]
            while len(remaining) > 0:
                if len(remaining) <= maxLength:
                    lines.append(remaining)
                    remaining = ""
                else:
                    wrappoint = remaining[:maxLength].rfind(" ")
                    if wrappoint == 0:
                        wrappoint = maxLength
                    lines.append(remaining[0:wrappoint])
                    remaining = remaining[wrappoint:]
            return lines

    
    ###############################################################################################
    #
    #    Battleye events handlers
    #    
    ###############################################################################################

    
    def OnPlayerChat(self, action, data):
        """
        #(Lobby) Bravo17: hello b3'
        #(Global) Bravo17: global channel

        Player has sent a message to other players
        """
        

        name, sep, message = data.partition(': ')
        name = name.strip()
        self.debug('Name = %s, Message = %s Name length = %s' % (name, message, len(name)))
        
        self.debug('Looking for client %s' % name)
        client = self.getClient(name)

        if client is None:
            self.warning("Could not get client :( %s" % traceback.extract_tb(sys.exc_info()[2]))
            return
        if client.cid == 'Server':
            # ignore chat events for Server
            return
        text = message

        # existing commands can be prefixed with a '/' instead of usual prefixes
        cmdPrefix = '!'
        cmd_prefixes = (cmdPrefix, '@', '&')
        admin_plugin = self.getPlugin('admin')
        if admin_plugin:
            cmdPrefix = admin_plugin.cmdPrefix
            cmd_prefixes = (cmdPrefix, admin_plugin.cmdPrefixLoud, admin_plugin.cmdPrefixBig)
        cmd_name = text[1:].split(' ', 1)[0].lower()
        if len(text) >= 2 and text[0] == '/':
            if text[1] in cmd_prefixes:
                text = text[1:]
            elif cmd_name in admin_plugin._commands:
                text = cmdPrefix + text[1:]

        event_type = b3.events.EVT_CLIENT_SAY
        return b3.events.Event(event_type, text, client)
        

    def OnPlayerLeave(self, action, data):
        """
        #Player #4 Kauldron disconnected
        Player has left the server
        """

        client = self.getClient(data[0])
        if client: 
            client.disconnect() # this triggers the EVT_CLIENT_DISCONNECT event
        return None

    def OnPlayerConnected(self, action, data):
        """
        # Player #0 Bravo17 (76.108.91.78:2304)
        Initial player connect message received
        """

        data = data.rpartition(')')[0]
        data, sep, ip = data.rpartition('(')
        ip = ip.partition(':')[0]
        cid, sep, name = data.partition(' ')
        name = name.strip() 
        self._client_auth_waiting['cid']=  (ip, name)
        return None
        
    def OnUnverifiedGUID(self, action, data):
        """
        #Player #0 Bravo17 - GUID: 80a5885ebe2420bab5e158a310fcbc7d (unverified)
        Players GUID has been found but not verified, no action to take
        """

        return None
        

    def OnVerifiedGUID(self, action, data):
        """
        #Verified GUID  (80a5885ebe2420bab5e158a310fcbc7d) of player #0 Bravo17
        Players GUID has been verified, auth player
        """

        guid = data.partition(')')[0]
        data = data .partition('#')[2]
        cid, sep, name = data.partition(' ')
        name = name.strip()
        ip = ''
        try:
            if self._client_auth_waiting['cid'][1] == name:
                ip = self._client_auth_waiting['cid'][0]
        except KeyError:
            pass
            
        client = self.clients.getByGUID(guid)
        if not client:
            self.debug('adding client')
            client = self.clients.newClient(cid, guid=guid, name=name, ip=ip)
            self.debug(client)
            # update client data
            client.name = name
            client.cid = cid
            if ip != '':
                client.ip = ip
            self.queueEvent(b3.events.Event(b3.events.EVT_CLIENT_JOIN, None, client))
                
        return None

    def OnServerMessage(self, action, data):
        """
        Request: Message from Server
        Effect: None, no messages from server are relevant
        """
        self.debug("Server Message")
        event_type = b3.events.EVT_CLIENT_SAY
        evt = b3.events.Event(event_type, None, None)
        #pass
        return
        
    def OnPlayerList(self, action, data):
        """
        Puts the playerlist in an array for getPlayerList to retrieve'
        """
        self.debug('Playerlist retrieved')
        self._player_list_from_server = data

        
    def OnBanList(self, action, data):
        """
        Puts the banlist in an array for unban to retrieve'
        """
        self._ban_list_from_server = data
        
        
    def OnBattleyeKick(self, action, data):
        """
        #Player #2 NZ (04b81a0bd914e7ba610ef3c0ffd66a1a) has been kicked by BattlEye: Script Restriction #107'
        Player has been kicked by Battleye
        """

        player, msg, reason = data.partition(') has been kicked by BattlEye: ')
        cid = player.partition(' ')[0]
        guid = player.rpartition('(')[2]
        name = data[len(cid)+1:-len(guid)-len(reason)-33]
        self.debug('Looking for client %s with GUID %s' % (name, guid))
        client = self.getClient(name, guid=guid, auth=False)
        if client: 
            client.disconnect() # this triggers the EVT_CLIENT_DISCONNECT event
        
    def OnUnknownEvent(self, action, data):
        return False
    ###############################################################################################
    #
    #    B3 Parser interface implementation
    #    
    ###############################################################################################


    def getClient(self, name, guid=None, cid=None, ip=None, auth=True):
        """Get a connected client from storage or create it
        B3 CID   <--> cid
        B3 GUID  <--> guid
        """
        client = None
        if guid:
            # try to get the client from the storage of already authed clients by guid
            client = self.clients.getByGUID(guid)
        if not client:
            # try to get the client from the storage of already authed clients by name
            client = self.clients.getByName(name)
        if auth and not client:
            if cid == 'Server':
                return self.clients.newClient('Server', guid='Server', name='Server', hide=True)
            if guid:
                client = self.clients.newClient(cid, guid=guid, name=name, ip=ip)
            else:
                return None
                
            self.queueEvent(b3.events.Event(b3.events.EVT_CLIENT_JOIN, None, client))
        return client
    
#Players on server:\n
#[#] [IP Address]:[Port] [Ping] [GUID] [Name]\n--------------------------------------------------\n
#0   76.108.91.78:2304     63   80a5885ebe2420bab5e158a310fcbc7d(OK) Bravo17\n
#0   192.168.0.100:2316    0    80a5885ebe2420bab5e158a310fcbc7d(OK) Bravo17 (Lobby)\n
#(1 players in total)'
    
    
    def getPlayerList(self, maxRetries=None):
        """return a dict which keys are cid and values a dict of player properties
        as returned by admin.listPlayers.
        Does not return client objects"""
        while self._server_response_lock:
            time.sleep(0.5)
        self._server_response_lock = True
        self.write(('players', ))
        timeout_time = time.time() + self._server_response_timeout
        while not self._player_list_from_server and time.time() < timeout_time:
            self.debug('Waiting for player list')
            time.sleep(0.1)
        
        if self._player_list_from_server:
            self.debug('Got player list')
            self._previous_player_list_from_server = self._player_list_from_server
            self._player_list_errors = 0
        else:
            self.debug("using previous player list")
            self._player_list_errors += 1
            self._player_list_from_server = self._previous_player_list_from_server
        self._server_response_lock = False
        
        if self._player_list_errors > 5:
            self._player_list_errors = 0
            self.debug('Too many player list errors - shutting down connection')
            self.close_battleye_connection()
        
        player_list = self._player_list_from_server.splitlines()
        self._player_list_from_server = ''
        self.debug('Playerlist is %s' % player_list)
        self.debug('Playerlist is %s long' % (len(player_list)-4))
        players = {}
        for i in range(3, len(player_list)-1):
            p = re.match(self._regPlayer_lobby, player_list[i])
            if p:
                pl = p.groupdict()
                self.debug('Player: %s' % pl)
                pl['lobby'] = True
                players[pl['cid']] = pl
            else:
                p = re.match(self._regPlayer, player_list[i])
                if p:
                    pl = p.groupdict()
                    self.debug('Player: %s' % pl)
                    pl['lobby'] = False
                    players[pl['cid']] = pl
                else:
                    self.debug('Not Matched: %s ' % player_list[i])

        self.debug('Players on server: %s' % players)
        return players


    def authorizeClients(self):
        """
        Authorise clients from player list
        """
        players = self.getPlayerList()
        self.verbose('authorizeClients() = %s' % players)

        for cid, p in players.iteritems():
            sp = self.clients.getByCID(cid)
            if sp:
                # Only set provided data, otherwise use the currently set data
                sp.ip   = p.get('ip', sp.ip)
                sp.pbid = p.get('pbid', sp.pbid)
                sp.guid = p.get('guid', sp.guid)
                sp.data = p
                newTeam = p.get('teamId', None)
                if newTeam is not None:
                    sp.team = self.getTeam(newTeam)
                sp.teamId = int(newTeam)
                sp.auth()


    def sync(self):
        """
        Sync clients with player list
        """
        plist = self.getPlayerList()
        mlist = {}

        for cid, c in plist.iteritems():
            client = self.clients.getByCID(cid)
            if client:
                if c['guid'] == client.guid:
                    self.debug('Client found on server %s' % client)
                    mlist[cid] = client
                    self.debug ('Lobby is %s ' % c['lobby'])
                    if c['lobby'] and client.team != self.getTeam('lobby'):
                        self.debug('Putting in Lobby')
                        client.team = self.getTeam('lobby')
                    elif client.team == self.getTeam('lobby') and not c['lobby']:
                        self.debug('Removing from Lobby')
                        client.team = self.getTeam('unknown')
            
                else:
                    # Wrong client in slot
                    self.debug('Removing %s from list' % client.name)
                    client.disconnect()
            else:
                if c['verified'] == 'OK':
                    self.debug('Look for client in storage')
                    cl = self.getClient(c['name'], guid=c['guid'], cid=c['cid'], ip=c['ip'])
                    if cl:
                        mlist[cid] = cl
                    
        # Now we need to remove any players that have left
        if self.clients:
            client_cid_list = []

            for cl in plist.values():
                client_cid_list.append(cl['cid'])

            for client in self.clients.getList():

                if client.cid not in client_cid_list:
                    self.debug('Removing %s from list' % client.name)
                    client.disconnect()
        self.queueEvent(b3.events.Event(b3.events.EVT_PLAYER_SYNC_COMPLETED, None, None))
        
        return mlist

    def say(self, msg):
        self.sayqueue.put(self.stripColors(msg))

    def saybig(self, msg):
        """\
        broadcast a message to all players in a way that will catch their attention.
        """
        self.say(msg)


    def kick(self, client, reason='', admin=None, silent=False, *kwargs):
        #'kick': ('kick ', '%(cid)s', '%(reason)s'),

        self.debug('kick reason: [%s]' % reason)
        if isinstance(client, str):
            self.write(self.getCommand('kick', cid=client, reason=reason[:80]))
            return
        
        if admin:
            fullreason = self.getMessage('kicked_by', self.getMessageVariables(client=client, reason=reason, admin=admin))
        else:
            fullreason = self.getMessage('kicked', self.getMessageVariables(client=client, reason=reason))
        fullreason = self.stripColors(fullreason)
        reason = self.stripColors(reason)

        self.write(self.getCommand('kick', cid=client.cid, reason=reason[:80]))

        if not silent and fullreason != '':
            self.say(fullreason)


    def message(self, client, text):
        try:
            if client is None:
                self.say(self.stripColors(text))
            elif client.cid is None:
                pass
            else:
                cmd_name = 'bigmessage' if self._settings['big_b3_private_responses'] else 'message'
                self.write(self.getCommand(cmd_name, message=self.stripColors(text), cid=client.cid, big_msg_duration=int(float(self._settings['big_msg_duration']))))
        except Exception, err:
            self.warning(err)


    def ban(self, client, reason='', admin=None, silent=False, *kwargs):
        """Permanent ban"""
        #'ban': ('ban ', '%(cid)s', '0', '%(reason)s'),
        #'banByGUID': ('ban ', '%(guid)s', '0', '%(reason)s'),

        self.debug('BAN : client: %s, reason: %s', client, reason)

        if admin:
            fullreason = self.getMessage('banned_by', self.getMessageVariables(client=client, reason=reason, admin=admin))
        else:
            fullreason = self.getMessage('banned', self.getMessageVariables(client=client, reason=reason))
        fullreason = self.stripColors(fullreason)
        reason = self.stripColors(reason)

        if self.ban_with_server:
            if client.cid is None:
                # ban by guid, this happens when we !permban @xx a player that is not connected
                self.debug('EFFECTIVE BAN : %s',self.getCommand('banByGUID', guid=client.guid, reason=reason[:80]))
                try:
                    self.write(self.getCommand('banByGUID', guid=client.guid, reason=reason[:80]))
                    self.debug(self.getCommand('banByGUID', guid=client.guid, reason=reason[:80]))
                    self.write(('writeBans',))
                    if admin:
                        admin.message('banned: %s (@%s) has been added to banlist' % (client.exactName, client.id))
                except CommandFailedError, err:
                    self.error(err)
            else:
                # ban by cid
                self.debug('EFFECTIVE BAN : %s',self.getCommand('ban', cid=client.cid, reason=reason[:80]))
                try:
                    #self.write(self.getCommand('ban', cid=client.cid, reason=reason[:80]))
                    self.debug(self.getCommand('ban', cid=client.cid, reason=reason[:80]))
                    self.write(('writeBans',))
                    if admin:
                        admin.message('banned: %s (@%s) has been added to banlist' % (client.exactName, client.id))
                except CommandFailedError, err:
                    self.error(err)

        
        if not silent and fullreason != '':
            self.say(fullreason)
        
        self.queueEvent(b3.events.Event(b3.events.EVT_CLIENT_BAN, {'reason': reason, 'admin': admin}, client))


    def unban(self, client, reason='', admin=None, silent=False, *kwargs):
        #'unban': ('removeBan', '%(ban_no)s'),
        #GUID Bans:
        #[#] [GUID] [Minutes left] [Reason]
        #----------------------------------------
        #0  b57cb4973da76f4588936416aae2de05 perm Script Detection: Gerk
        #1  8ac69e7189ecd2ff4235142feff0bd26 perm Script Detection: setVehicleInit DoThis;

        self.debug('UNBAN: Name: %s, Ip: %s, Guid: %s' %(client.name, client.ip, client.guid))
        while self._server_response_lock:
            time_sleep(0.5)
        self._server_response_lock = True
        self.write( ('bans',) )
        timeout_time = time.time() + self._server_response_timeout
        while not self._ban_list_from_server and time.time() < timeout_time:
            self.debug('Waiting for ban list')
            time.sleep(0.1)
        self._server_response_lock = False
        
        if self._ban_list_from_server:
            self.debug('Got ban list')
        else:
            admin.message('Failed to get ban list in time')
            return
        ban_list = self._ban_list_from_server.splitlines()
        self._ban_list_from_server = ''
        ban_no_list = []
        for line in ban_list:
            if client.guid in line:
                ban_no = line.partition(' ')[0]
                ban_no_list.append(ban_no)
        
        if ban_no_list:
            for ban_no in ban_no_list:
                try:
                    self.write(self.getCommand('unban', ban_no=ban_no, reason=reason), needConfirmation=True)
                    #self.verbose(response)
                    self.verbose('UNBAN: Removed ban (%s) guid from banlist' % ban_no)
                    if admin:
                        admin.message('Unbanned: Removed %s guid from banlist' % client.exactName)
                except CommandFailedError, err:
                    if "NotInList" in err.message:
                        pass
                    else:
                        raise
            self.write(('writeBans',))

    def tempban(self, client, reason='', duration=2, admin=None, silent=False, *kwargs):
        #'tempban': ('ban ', '%(cid)s', '%(duration)d', '%(reason)s'),
        #'tempbanByGUID': ('ban ', '%(guid)s', '%(duration)d', '%(reason)s'),
        duration = b3.functions.time2minutes(duration)
        if duration < 1:
            # Ban with length of zero will permban a player with Battleye, so do not activate the ban
            return

        if admin:
            fullreason = self.getMessage('temp_banned_by', self.getMessageVariables(client=client, reason=reason, admin=admin, banduration=b3.functions.minutesStr(duration)))
        else:
            fullreason = self.getMessage('temp_banned', self.getMessageVariables(client=client, reason=reason, banduration=b3.functions.minutesStr(duration)))
        fullreason = self.stripColors(fullreason)
        reason = self.stripColors(reason)

        if self.ban_with_server:
            if client.cid is None:
                # ban by guid, this happens when we !tempban @xx a player that is not connected
                try:
                    self.write(self.getCommand('tempbanByGUID', guid=client.guid, duration=duration, reason=reason[:80]))
                    self.write(('writeBans',))
                except CommandFailedError, err:
                    if admin:
                        admin.message("server replied with error %s" % err.message[0])
                    else:
                        self.error(err)
            else:
                try:
                    self.write(self.getCommand('tempban', cid=client.cid, duration=duration, reason=reason[:80]))
                    self.write(('writeBans',))
                except CommandFailedError, err:
                    if admin:
                        admin.message("server replied with error %s" % err.message[0])
                    else:
                        self.error(err)

        if not silent and fullreason != '':
            self.say(fullreason)

        self.queueEvent(b3.events.Event(b3.events.EVT_CLIENT_BAN_TEMP, {'reason': reason, 
                                                              'duration': duration, 
                                                              'admin': admin}
                                        , client))

 
    def getMap(self):
        """Return the current level name (not easy map name)"""
        pass


    def getMaps(self):
        """Return the map list for the current rotation. (as easy map names)
        This does not return all available maps
        """
        pass


    def rotateMap(self):
        """\
        load the next map/level
        """
        pass

    def changeMap(self, map_name, gamemode_id=None):
        """\
        load a given map/level
        return a list of suggested map names in cases it fails to recognize the map that was provided
        """
        pass

        
    def getPlayerPings(self):
        """Ask the server for a given client's pings
        """
        pass


    def getPlayerScores(self):
        """Ask the server for a given client's team
        """
        scores = {}
        pass

    def getTeam(self, team):
        if team == '0':
            result = b3.TEAM_RED
        elif team == '1':
            result = b3.TEAM_BLUE
        elif team == 'lobby':
            result = b3.TEAM_SPEC
        elif team == 'unknown':
            result = b3.TEAM_UNKNOWN
        else:
            result = b3.TEAM_UNKNOWN
        return result
        
        
    ###############################################################################################
    #
    #    Other methods
    #    
    ###############################################################################################


    def getFullBanList(self):
        """
        query the Battleye game server and return a BanlistContent object containing all bans stored on the game
        server memory.
        """
        response = BanlistContent()
        offset = 0
        tmp = self.write(('banList.list', offset))
        tmp_num_bans = len(BanlistContent(tmp))
        while tmp_num_bans:
            response.append(tmp)
            tmp = self.write(('banList.list', len(response)))
            tmp_num_bans = len(BanlistContent(tmp))
        return response

    def load_conf_max_say_line_length(self):
        if self.config.has_option(self.gameName, 'max_say_line_length'):
            try:
                maxlength = self.config.getint(self.gameName, 'max_say_line_length')
                if maxlength > self.SAY_LINE_MAX_LENGTH:
                    self.warning('max_say_line_length cannot be greater than %s' % self.SAY_LINE_MAX_LENGTH)
                    maxlength = self.SAY_LINE_MAX_LENGTH
                if maxlength < 20:
                    self.warning('max_say_line_length is way too short. using minimum value 20')
                    maxlength = 20
                self._settings['line_length'] = maxlength
                self._settings['min_wrap_length'] = maxlength
            except Exception, err:
                self.error('failed to read max_say_line_length setting "%s" : %s' % (
                    self.config.get(self.gameName, 'max_say_line_length'), err))
        self.debug('line_length: %s' % self._settings['line_length'])


    def load_config_message_delay(self):
        if self.config.has_option(self.gameName, 'message_delay'):
            try:
                delay_sec = self.config.getfloat(self.gameName, 'message_delay')
                if delay_sec > 3:
                    self.warning('message_delay cannot be greater than 3')
                    delay_sec = 3
                if delay_sec < .5:
                    self.warning('message_delay cannot be less than 0.5 second.')
                    delay_sec = .5
                self._settings['message_delay'] = delay_sec
            except Exception, err:
                self.error(
                    'failed to read message_delay setting "%s" : %s' % (self.config.get(self.gameName, 'message_delay'), err))
        self.debug('message_delay: %s' % self._settings['message_delay'])
        
        
    def load_protocol_logging(self):
        """
        Allow extra logging from protocol.py to be activaed
        """
        if self.config.has_option('b3', 'protocol_log'):
            logfile = self.config.getpath('b3', 'protocol_log')
            return logfile
        else:
            return None
        
    def shutdown(self):
        """Shutdown B3"""
        try:
            if self.working and self.exiting.acquire():
                self.bot('Shutting down...')
                self.working = False
                self.queueEvent(b3.events.Event(b3.events.EVT_STOP, None))
                for k,plugin in self._plugins.items():
                    plugin.parseEvent(b3.events.Event(b3.events.EVT_STOP, ''))
                if self._cron:
                    self._cron.stop()

                self.bot('Shutting down database connections...')
                self.storage.shutdown()
                #self.exiting.release()
        except Exception, e:
            self.error(e)

            
    def restart(self):
        """Stop B3 with the restart exit status (221)"""
        self.shutdown()

        time.sleep(5)

        self.bot('Restarting...')
        self.exitcode = 221
        sys.exit(221)
