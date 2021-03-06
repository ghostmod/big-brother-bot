#
# BigBrotherBot(B3) (www.bigbrotherbot.net)
# Copyright (C) 2005 Michael "ThorN" Thornton
# 
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.    See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA    02110-1301    USA
#
# CHANGELOG
# 12/08/2012 - 1.4.11 - Courgette
# * will provide more debugging info about errors while generating the XML document
# 05/05/2012 - 1.4.10 - Courgette
# * fixes reading config options 'svar_table' and 'client_table'
# 19/07/2011 - 1.4.9 - Freelander
# * fix errors during map change
# 25/04/2011 - 1.4.8 - Courgette
# * in config file, settings/output_file can now use shortcuts such as @b3 and @conf
# 17/04/2011 - 1.4.7 - Courgette
# * XML file generated is now using UTF-8 encoding
# 17/04/2011 - 1.4.6 - Courgette
# * unicode compliant
# 30/03/2011 - 1.4.5 - SGT
# * bugfix camelCasing timeLimit and fragLimit
# 06/01/2011 - 1.4.4 - Gammelbob
# * additionally stores current svars and clients in database
# 13/08/2010 - 1.4.3 - xlr8or
# * Added running roundtime and maptime
# 08/08/2010 - 1.4.2 - xlr8or
# * Moved 'Game section' before 'Clients section', XLRstats needs gameinfo before it adds clients to the playerlist
# 21/03/2010 - 1.4.1 - Courgette
# * does not fail if there is no player score available
# * make errors more verbose
# 23/11/2009 - 1.4.0 - Courgette
# on bot shutdown, write an empty status.xml document.
# add tests
# 03/11/2009 - 1.3.1 - Bakes
# XML code is now produced through xml.dom.minidocument rather than concatenation. This has a number of advantages.
# 03/11/2009 - 1.3.0 - Bakes
# Combined statusftp and status. Use syntax ftp://user:password@host/path/to/status.xml
# 11/02/2009 - 1.2.7 - xlr8or
# If masked show masked level instead of real level
# 11/02/2009 - 1.2.6 - xlr8or
# Sanitized xml data, cleaning ascii < 32 and > 126 (func is in functions.py)
# 21/11/2008 - 1.2.5 - Anubis
# Added PlayerScores
# 12/03/2008 - 1.2.4 - Courgette
# Properly escape strings to ensure valid xml
# 11/30/2005 - 1.2.3 - ThorN
# Use PluginCronTab instead of CronTab
# 8/29/2005 - 1.2.0 - ThorN
# Converted to use new event handlers

__author__    = 'ThorN'
__version__ = '1.4.11'

import b3
import time
import os
import StringIO
import re
import b3.plugin
import b3.cron
from cgi import escape
from ftplib import FTP
from b3 import functions
from xml.dom.minidom import Document
from b3.functions import sanitizeMe

#--------------------------------------------------------------------------------------------------
class StatusPlugin(b3.plugin.Plugin):
    _tkPlugin = None
    _cronTab = None
    _ftpstatus = False
    _ftpinfo = None
    _enableDBsvarSaving = False
    _svarTable = 'current_svars'
    _enableDBclientSaving = False
    _clientTable = 'current_clients'
    
    def onLoadConfig(self):
        if self.config.get('settings','output_file')[0:6] == 'ftp://':
            self._ftpinfo = functions.splitDSN(self.config.get('settings','output_file'))
            self._ftpstatus = True
        else:        
            self._outputFile = self.config.getpath('settings', 'output_file')
                
        self._tkPlugin = self.console.getPlugin('tk')
        self._interval = self.config.getint('settings', 'interval')
        try:
            self._enableDBsvarSaving = self.config.getboolean('settings', 'enableDBsvarSaving')
        except:
            self._enableDBsvarSaving = False
        try:
            self._enableDBclientSaving = self.config.getboolean('settings', 'enableDBclientSaving')
        except:
            self._enableDBclientSaving = False

        try:
            self._svarTable = self.config.get('settings', 'svar_table')
            self.debug('Using custom table for saving server svars: %s' % self._svarTable)
        except:
            self.debug('Using default table for saving server svars: %s' % self._svarTable)
        try:
            self._clientTable = self.config.get('settings', 'client_table')
            self.debug('Using custom table for saving current clients: %s' % self._clientTable)
        except:
            self.debug('Using default table for saving current clients: %s' % self._clientTable)

        if self._enableDBsvarSaving:
            sql = "CREATE TABLE IF NOT EXISTS `%s` (`id` int(11) NOT NULL auto_increment,`name` varchar(255) NOT NULL,`value` varchar(255) NOT NULL, PRIMARY KEY  (`id`), UNIQUE KEY `name` (`name`)) ENGINE=MyISAM DEFAULT CHARSET=utf8 AUTO_INCREMENT=1 ;" % (self._svarTable)
            self.console.storage.query(sql)
        if self._enableDBclientSaving:
            sql = "CREATE TABLE IF NOT EXISTS `%s` (`id` INT(3) NOT NULL AUTO_INCREMENT,`Updated` VARCHAR( 255 ) NOT NULL ,`Name` VARCHAR( 255 ) NOT NULL ,`Level` VARCHAR( 255 ) NOT NULL ,`DBID` VARCHAR( 255 ) NOT NULL ,`CID` VARCHAR( 255 ) NOT NULL ,`Joined` VARCHAR( 255 ) NOT NULL ,`Connections` VARCHAR( 255 ) NOT NULL ,`State` VARCHAR( 255 ) NOT NULL ,`Score` VARCHAR( 255 ) NOT NULL ,`IP` VARCHAR( 255 ) NOT NULL ,`GUID` VARCHAR( 255 ) NOT NULL ,`PBID` VARCHAR( 255 ) NOT NULL ,`Team` VARCHAR( 255 ) NOT NULL ,`ColorName` VARCHAR( 255 ) NOT NULL, PRIMARY KEY (`id`)) ENGINE = MYISAM DEFAULT CHARSET=utf8 AUTO_INCREMENT=1;" % (self._clientTable)
            self.console.storage.query(sql)

        if self._cronTab:
            # remove existing crontab
            self.console.cron - self._cronTab

        self._cronTab = b3.cron.PluginCronTab(self, self.update, '*/%s' % self._interval)
        self.console.cron + self._cronTab

    def onEvent(self, event):
        if event.type == b3.events.EVT_STOP:
            self.info('B3 stop/exit.. updating status')
            # create an empty status document
            xml = Document()
            b3status = xml.createElement("B3Status")
            b3status.setAttribute("Time", time.asctime())
            xml.appendChild(b3status)
            self.writeXML(xml.toprettyxml(indent="        "))

    def update(self):
        clients = self.console.clients.getList()
        scoreList = self.console.getPlayerScores() 
                 
        self.verbose('Building XML status')
        xml = Document()
        # --- Main section
        b3status = xml.createElement("B3Status")
        b3status.setAttribute("Time", time.asctime())
        xml.appendChild(b3status)

        # --- Game section
        c = self.console.game
        gamename = ''
        gametype = ''
        mapname = ''
        timelimit = ''
        fraglimit = ''
        capturelimit = ''
        rounds = ''
        roundTime = ''
        mapTime = ''
        if c.gameName:
            gamename = c.gameName
        if c.gameType:
            gametype = c.gameType
        if c.mapName:
            mapname = c.mapName
        if c.timeLimit:
            timelimit = c.timeLimit
        if c.fragLimit:
            fraglimit = c.fragLimit
        if c.captureLimit:
            capturelimit = c.captureLimit
        if c.rounds:
            rounds = c.rounds
        if c.roundTime:
            roundTime = c.roundTime()
        if c.mapTime():
            mapTime = c.mapTime()
        game = xml.createElement("Game")
        game.setAttribute("Name", str(gamename))
        game.setAttribute("Type", str(gametype))
        game.setAttribute("Map", str(mapname))
        game.setAttribute("TimeLimit", str(timelimit))
        game.setAttribute("FragLimit", str(fraglimit))
        game.setAttribute("CaptureLimit", str(capturelimit))
        game.setAttribute("Rounds", str(rounds))
        game.setAttribute("RoundTime", str(roundTime))
        game.setAttribute("MapTime", str(mapTime))
        b3status.appendChild(game)

        for k,v in self.console.game.__dict__.items():
            data = xml.createElement("Data")
            data.setAttribute("Name", str(k))
            data.setAttribute("Value", str(v))
            game.appendChild(data)
            if self._enableDBsvarSaving:
                #remove forbidden sql characters
                _k = re.sub("'", "", str(k))
                _v = re.sub("'", "", str(v))[:255] # length of the database varchar field
                sql = "INSERT INTO %s (name, value) VALUES ('%s','%s') ON DUPLICATE KEY UPDATE value = VALUES(value);" % (self._svarTable, _k, _v)
                try:
                    self.console.storage.query(sql)
                except:
                    self.error('Error: inserting svars. sqlqry=%s' % (sql))
        if self._enableDBsvarSaving:
            sql = "INSERT INTO %s (name, value) VALUES ('lastupdate',UNIX_TIMESTAMP()) ON DUPLICATE KEY UPDATE value = VALUES(value);" % (self._svarTable)
            try:
                self.console.storage.query(sql)
            except:
                self.error('Error: inserting svars. sqlqry=%s' % (sql))
        # --- End Game section        

        # --- Clients section
        b3clients = xml.createElement("Clients")
        b3clients.setAttribute("Total", str(len(clients)))
        b3status.appendChild(b3clients)

        if self._enableDBclientSaving:
            # empty table current_clients
            sql = "TRUNCATE TABLE `%s`;" % (self._clientTable)
            self.console.storage.query(sql)

        for c in clients:
            if not c.name:
                c.name = "@"+str(c.id)
            if c.exactName == "^7":
                c.exactName = "@"+str(c.id)+"^7"

            if not c.maskedLevel:
                _level = c.maxLevel
            else:
                _level = c.maskedLevel

            try:
                client = xml.createElement("Client")
                client.setAttribute("Name", sanitizeMe(c.name))
                client.setAttribute("ColorName", sanitizeMe(c.exactName))
                client.setAttribute("DBID", str(c.id))
                client.setAttribute("Connections", str(c.connections))
                client.setAttribute("CID", c.cid)
                client.setAttribute("Level", str(_level))
                if c.guid:
                    client.setAttribute("GUID", c.guid)
                else:
                    client.setAttribute("GUID", '')
                if c.pbid:
                    client.setAttribute("PBID", c.pbid)
                else:
                    client.setAttribute("PBID", '')
                client.setAttribute("IP", c.ip)
                client.setAttribute("Team", str(c.team))
                client.setAttribute("Joined", str(time.ctime(c.timeAdd)))
                client.setAttribute("Updated", str(time.ctime(c.timeEdit)))
                if scoreList and c.cid in scoreList:
                    client.setAttribute("Score", str(scoreList[c.cid]))
                client.setAttribute("State", str(c.state))
                if self._enableDBclientSaving:
                    qryBuilderKey = ""
                    qryBuilderValue = ""
                    # get our attributes
                    for k, v in client.attributes.items():
                        # build the qrystring
                        qryBuilderKey = "%s%s," % (qryBuilderKey, k)
                        qryBuilderValue = "%s'%s'," % (qryBuilderValue, v)
                    # cut last ,
                    qryBuilderKey = qryBuilderKey[:-1]
                    qryBuilderValue = qryBuilderValue[:-1]
                    # and insert
                    try:
                        sql = "INSERT INTO %s (%s) VALUES (%s); " % (self._clientTable, qryBuilderKey, qryBuilderValue)
                        self.console.storage.query(sql)
                    except:
                        self.error('Error: inserting clients. sqlqry=%s' % (sql))

                b3clients.appendChild(client)
                for k,v in c.data.iteritems():
                    data = xml.createElement("Data")
                    data.setAttribute("Name", "%s" % k)
                    data.setAttribute("Value", sanitizeMe(v))
                    client.appendChild(data)
                        
                if self._tkPlugin:
                    if hasattr(c, 'tkplugin_points'):
                        tkplugin = xml.createElement("TkPlugin")
                        tkplugin.setAttribute("Points", str(c.var(self, 'points')))
                        client.appendChild(tkplugin)            
                        if hasattr(c, 'tkplugin_attackers'):
                            for acid,points in c.var(self, 'attackers').value.items():
                                try:
                                    attacker = xml.createElement("Attacker")
                                    attacker.setAttribute("Name", sanitizeMe(self.console.clients[acid].name))
                                    attacker.setAttribute("CID", str(acid))
                                    attacker.setAttribute("Points", str(points))
                                    tkplugin.appendChild(attacker)
                                except:
                                    pass
                                
            except Exception, err:
                self.error('XML Failed: %r' % err, exc_info=err)
                pass

        # --- End Clients section

        self.writeXML(xml.toprettyxml(encoding="UTF-8", indent="        "))

    def writeXML(self, xml):
        if self._ftpstatus == True:
            self.debug('Uploading XML status to FTP server')
            ftp=FTP(self._ftpinfo['host'],self._ftpinfo['user'],passwd=self._ftpinfo['password'])
            ftp.cwd(os.path.dirname(self._ftpinfo['path']))
            ftpfile = StringIO.StringIO()
            ftpfile.write(xml)
            ftpfile.seek(0)
            ftp.storbinary('STOR '+os.path.basename(self._ftpinfo['path']), ftpfile)
        else:
            self.debug('Writing XML status to %s', self._outputFile)
            f = file(self._outputFile, 'w')
            f.write(xml)
            f.close()
            
            
if __name__ == '__main__':
    from b3.fake import fakeConsole
    from b3.fake import joe
    from b3.fake import simon
    
    p = StatusPlugin(fakeConsole, "@b3/conf/plugin_status.xml")
    p.onStartup()
    p.update()
    
    while True: pass
    
