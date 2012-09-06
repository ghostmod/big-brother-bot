# -*- encoding: utf-8 -*-
#
# BigBrotherBot(B3) (www.bigbrotherbot.net)
# Copyright (C) 2012 Courgette
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
import unittest2 as unittest
from mock import Mock, patch
from b3.fake import FakeClient
from b3.parsers.arma2 import Arma2Parser
from b3.config import XmlConfigParser



class Arma2TestCase(unittest.TestCase):
    """
    Test case that is suitable for testing Arma2 parser specific features
    """

    @classmethod
    def setUpClass(cls):
        from b3.parsers.battleye.abstractParser import AbstractParser
        from b3.fake import FakeConsole
        AbstractParser.__bases__ = (FakeConsole,)
        # Now parser inheritance hierarchy is :
        # Arma2Parser -> AbstractParser -> FakeConsole -> Parser

    def tearDown(self):
        if hasattr(self, "parser"):
            self.parser.working = False





class Test_game_events_parsing(Arma2TestCase):

    def setUp(self):
        """ran before each test"""
        self.conf = XmlConfigParser()
        self.conf.loadFromString("""
                <configuration>
                </configuration>
            """)
        self.parser = Arma2Parser(self.conf)
        self.parser.output = Mock() # mock Rcon

        self.evt_queue = []
        def queue_event(evt):
            self.evt_queue.append(evt)
        self.queueEvent_patcher = patch.object(self.parser, "queueEvent", wraps=queue_event)
        self.queueEvent_mock = self.queueEvent_patcher.start()

        self.parser.startup()


    def tearDown(self):
        """ran after each test to clean up"""
        Arma2TestCase.tearDown(self)
        self.queueEvent_patcher.stop()
        if hasattr(self, "parser"):
            self.parser.working = False


    def clear_events(self):
        """
        clear the event queue, so when assert_has_event is called, it will look only at the newly caught events.
        """
        self.evt_queue = []


    def assert_has_event(self, event_type, data=None, client=None, target=None):
        """
        assert that self.evt_queue contains at least one event for the given type that has the given characteristics.
        """
        assert isinstance(event_type, basestring)

        def assert_event_equals(expected_event, actual_event):
            if expected_event is None:
                self.assertIsNone(actual_event)
            self.assertEqual(expected_event.type, actual_event.type, "expecting type %s, but got %s" %
                                                                     (self.parser.getEventKey(expected_event.type), self.parser.getEventKey(actual_event.type)))
            self.assertEqual(expected_event.client, actual_event.client, "expecting client %s, but got %s" % (expected_event.client, actual_event.client))
            self.assertEqual(expected_event.target, actual_event.target, "expecting target %s, but got %s" % (expected_event.target, actual_event.target))
            self.assertEqual(expected_event.data, actual_event.data, "expecting data %s, but got %s" % (expected_event.data, actual_event.data))

        expected_event = self.parser.getEvent(event_type, data, client, target)
        if not len(self.evt_queue):
            self.fail("expecting %s. Got no event instead" % expected_event)
        elif len(self.evt_queue) == 1:
            assert_event_equals(expected_event, self.evt_queue[0])
#            self.assertEqual(str(expected_event), str(self.evt_queue[0]))
        else:
            for evt in self.evt_queue:
                try:
                    assert_event_equals(expected_event, evt)
                    return
                except Exception:
                    pass
#                if str(expected_event) == str(evt):
#                    return
            self.fail("expecting event %s. Got instead: %s" % (expected_event, map(str, self.evt_queue)))


    ################################################################################################################

    def test_player_connected(self):
        # GIVEN
        self.clear_events()
        # WHEN
        self.parser.routeBattleyeMessagePacket("""Player #0 Bravo17 (76.108.91.78:2304) connected""")
        # THEN
        self.assertEqual(1, len(self.evt_queue))
        event = self.evt_queue[0]
        self.assertEqual(self.parser.getEventID("EVT_CLIENT_CONNECT"), event.type)
        self.assertEqual("Bravo17", event.client.name)
        self.assertEqual("0", event.client.cid)
        self.assertEqual("76.108.91.78", event.client.ip)


    def test_Verified_guid__with_connected_player(self):
        # GIVEN
        bravo17 = FakeClient(self.parser, name="Bravo17")
        bravo17.connects("0")
        self.clear_events()
        # WHEN
        self.parser.routeBattleyeMessagePacket("""Verified GUID (80a5885ebe2420bab5e158a310fcbc7d) of player #0 Bravo17""")
        # THEN
        self.assert_has_event("EVT_CLIENT_AUTH", data=bravo17, client=bravo17)


    def test_Verified_guid__with_unknown_player(self):
        # GIVEN
        self.clear_events()
        # WHEN
        self.parser.routeBattleyeMessagePacket("""Verified GUID (80a5885ebe2420bab5e158a310fcbc7d) of player #0 Bravo17""")
        # THEN
        self.assertTrue(len(self.evt_queue))
        event = self.evt_queue[0]
        self.assertEqual(self.parser.getEventID("EVT_CLIENT_CONNECT"), event.type)
        self.assertEqual("Bravo17", event.client.name)
        self.assertEqual("0", event.client.cid)
        bravo17 = event.client
        self.assert_has_event("EVT_CLIENT_AUTH", data=bravo17, client=bravo17)


    def test_player_disconnect(self):
        # GIVEN
        bravo17 = FakeClient(self.parser, name="Bravo17", guid="80a5885ebe2420bab5e158a310fcbc7d")
        bravo17.connects("12")
        self.clear_events()
        # WHEN
        self.parser.routeBattleyeMessagePacket("""Player #12 Bravo17 disconnected""")
        # THEN
        self.assert_has_event("EVT_CLIENT_DISCONNECT", client=bravo17, data='12')


    def test_Lobby_chat(self):
        # GIVEN
        bravo17 = FakeClient(self.parser, name="Bravo17", guid="80a5885ebe2420bab5e158a310fcbc7d")
        bravo17.connects("12")
        self.clear_events()
        # WHEN
        self.parser.routeBattleyeMessagePacket("""(Lobby) Bravo17: hello b3""")
        # THEN
        self.assert_has_event("EVT_CLIENT_SAY", client=bravo17, data='hello b3 (Lobby)')


    def test_Global_chat(self):
        # GIVEN
        bravo17 = FakeClient(self.parser, name="Bravo17", guid="80a5885ebe2420bab5e158a310fcbc7d")
        bravo17.connects("12")
        self.clear_events()
        # WHEN
        self.parser.routeBattleyeMessagePacket("""(Global) Bravo17: global channel""")
        # THEN
        self.assert_has_event("EVT_CLIENT_SAY", client=bravo17, data='global channel (Global)')


    def test_Direct_chat(self):
        # GIVEN
        bravo17 = FakeClient(self.parser, name="Bravo17", guid="80a5885ebe2420bab5e158a310fcbc7d")
        bravo17.connects("12")
        self.clear_events()
        # WHEN
        self.parser.routeBattleyeMessagePacket("""(Direct) Bravo17: test direct channel""")
        # THEN
        self.assert_has_event("EVT_CLIENT_SAY", client=bravo17, data='test direct channel (Direct)')


    def test_Vehicule_chat(self):
        # GIVEN
        bravo17 = FakeClient(self.parser, name="Bravo17", guid="80a5885ebe2420bab5e158a310fcbc7d")
        bravo17.connects("12")
        self.clear_events()
        # WHEN
        self.parser.routeBattleyeMessagePacket("""(Vehicle) Bravo17: test vehicle channel""")
        # THEN
        self.assert_has_event("EVT_CLIENT_SAY", client=bravo17, data='test vehicle channel (Vehicle)')


    def test_Group_chat(self):
        # GIVEN
        bravo17 = FakeClient(self.parser, name="Bravo17", guid="80a5885ebe2420bab5e158a310fcbc7d")
        bravo17.connects("12")
        self.clear_events()
        # WHEN
        self.parser.routeBattleyeMessagePacket("""(Group) Bravo17: test group channel""")
        # THEN
        self.assert_has_event("EVT_CLIENT_SAY", client=bravo17, data='test group channel (Group)')


    def test_Side_chat(self):
        # GIVEN
        bravo17 = FakeClient(self.parser, name="Bravo17", guid="80a5885ebe2420bab5e158a310fcbc7d")
        bravo17.connects("12")
        self.clear_events()
        # WHEN
        self.parser.routeBattleyeMessagePacket("""(Side) Bravo17: test side channel""")
        # THEN
        self.assert_has_event("EVT_CLIENT_SAY", client=bravo17, data='test side channel (Side)')


    def test_Command_chat(self):
        # GIVEN
        bravo17 = FakeClient(self.parser, name="Bravo17", guid="80a5885ebe2420bab5e158a310fcbc7d")
        bravo17.connects("12")
        self.clear_events()
        # WHEN
        self.parser.routeBattleyeMessagePacket("""(Command) Bravo17: test command channel""")
        # THEN
        self.assert_has_event("EVT_CLIENT_SAY", client=bravo17, data='test command channel (Command)')


    def test_player_connected_utf8(self):
        # GIVEN
        self.clear_events()
        # WHEN routeBattleyeMessagePacket is given a UTF-8 encoded message
        self.parser.routeBattleyeMessagePacket(u"""Player #0 F00Åéxx (11.1.1.8:2304) connected""".encode(encoding="UTF-8"))
        # THEN
        self.assertEqual(1, len(self.evt_queue))
        event = self.evt_queue[0]
        self.assertEqual(self.parser.getEventID("EVT_CLIENT_CONNECT"), event.type)
        self.assertEqual(u"F00Åéxx", event.client.name)
        self.assertEqual("0", event.client.cid)
        self.assertEqual("11.1.1.8", event.client.ip)

