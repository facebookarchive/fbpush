from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals
import copy
import mock
import os
import random
import time
import unittest
import xml.dom.minidom

from fbpush import main, push, config

# Uncomment if you don't want to see the print messages
# in main.py printed during the run of unittest suite
# sys.stdout = open(os.devnull, 'w')


class FBPushScreenTestCase(unittest.TestCase):

    def setUp(self):
        # First we mock the keys we need to initialize a Screen instance
        curses_mock = ['start_color', 'init_scr', 'curs_set', 'cbreak',
                       'nocbreak', 'noecho', 'color_pair']

        for key in curses_mock:
            push.curses.key = mock.MagicMock()

        # Mock other curses methods
        self.connections = []
        self.mock_initscr = mock.create_autospec(push.curses.initscr())
        self.mock_initscr.getmaxyx.return_value = (1, 1)
        push.logger = mock.MagicMock()

        # Initialize the Screen instance
        self.screen = push.Screen(self.connections, self.mock_initscr)
        # Since the initscr of curses is just a wrapper over its c
        # counterpart it ends up calling methods which make us lose control
        # of the terminal in which the unittests run. To gain it back revert
        # the function calls.
        self.screen.close()

    @mock.patch.object(push.Screen, 'close', create=True)
    def test_connectionLost(self, mock_close):
        # This checks whether the Screen instance closes properly
        # calling all the methods when connectionLost is called.
        self.screen.connectionLost('abc')
        msg = 'Screen.connectionLost() called. Reason:%s'
        push.logger.error.assert_called_with(msg, 'abc')

        mock_close.assert_called_once_with()

    def test_repaint(self):
        # TODO
        pass

    @mock.patch('fbpush.push.open', create=True)
    def test_statusPage(self, mock_open):
        # This methods checks the statusPage method which
        # returns the status lines for each connection
        kwargs = {}
        kwargs['screen'] = self.screen
        kwargs['connections'] = self.connections
        kwargs['device'] = mock.MagicMock()
        kwargs['username'] = config.username
        kwargs['password'] = config.password
        kwargs['device'].name = 'device1'
        kwargs['start_time'] = time.time()
        kwargs['all_diffs'] = 'abc'
        kwargs['diff_fh'] = 'abc'
        configlet = mock.MagicMock()
        configlet.name = 'configlet1'

        jnx = push.JunoscriptRpc(**kwargs)
        jnx.addConfiglets([configlet])

        # Need to add another connection detail as the list is
        # initially empty
        jnx.progress_bar = '==>'
        jnx.error_msg = 'no_error'
        self.connections.append(jnx)

        expected = [u'device1(configlet1): ==> [no_error]']
        lx = self.screen.statusPage()
        # Assert the expected status from the configlet
        # (there is just one currently) equal to received
        # status
        self.assertEqual(lx, expected)

    @mock.patch.object(push.Screen, 'getWaitingConnections')
    def test_quitPage(self, mock_getWaitingConnections):
        """This tests the quitPage method of Screen class."""

        waiting_connections = []
        # Add elements to self.connections
        device_len = random.randint(1, 20)
        device_names = []
        configlet = mock.MagicMock()
        configlet.device = mock.MagicMock()

        for i in range(device_len):
            configlet.device.name = 'configlet' + str(i)
            device_names.append(configlet.device.name)
            curr_configlet = copy.deepcopy(configlet)

            waiting_connections.append(curr_configlet)

        mock_getWaitingConnections.return_value = waiting_connections

        lines = self.screen.quitPage()

        lines = lines[3:device_len + 3]
        self.assertEqual(lines, device_names)

    def test_getWaitingConnections(self):
        """This tests the getWaitingConnections method."""
        allowed_device_len = random.randint(1, 10)
        # First we add valid elements to the self.connections
        for i in range(allowed_device_len):

            kwargs = {}
            kwargs['screen'] = mock.MagicMock()
            kwargs['connections'] = self.connections
            kwargs['device'] = mock.MagicMock()
            kwargs['username'] = config.username
            kwargs['password'] = config.password
            kwargs['device'].name = 'device1'
            kwargs['start_time'] = time.time()
            kwargs['all_diffs'] = 'abc'
            kwargs['diff_fh'] = 'abc'

            jnx = push.JunoscriptRpc(**kwargs)
            jnx.fsm = main.FSM_PushJnx(init_state=main.FSM_PushJnx.ST_START)

            jnx.device = mock.MagicMock()
            jnx.device.vendor = mock.MagicMock()
            jnx.device.vendor.name = 'juniper'

            allowed_states = [jnx.fsm.ST_WAIT_FOR_CONFIRM, jnx.fsm.ST_ROLLBACK,
                              jnx.fsm.ST_ROLLBACK_TESTING,
                              jnx.fsm.ST_ROLLBACK_COMMIT,
                              jnx.fsm.ST_ROLLBACK_COMMIT_TESTING]

            curr_allowed_state = random.randint(1, 5) - 1

            jnx.fsm.state = allowed_states[curr_allowed_state]
            self.connections.append(copy.deepcopy(jnx))

        # Introducing an element with invalid state to the list
        jnx.fsm.state = 1000
        self.connections.append(copy.deepcopy(jnx))

        valid_list = self.screen.getWaitingConnections()

        # Since the last element of self.connections is invalid
        # the valid_list must be equal to self.connections[:-1]
        self.assertEqual(valid_list, self.connections[:-1])

    def test_doRead(self):
        # TODO
        pass


class FBPushJunoscriptRpcTestCase(unittest.TestCase):
    @mock.patch('fbpush.push.open', create=True)
    def setUp(self, mock_open):
        self.screen = mock.MagicMock()
        self.connections = []

        # A sample device
        self.device = mock.MagicMock()
        self.device.name = 'device1'

        # attributes needed to initialize JunoscriptRpc
        kwargs = {}
        kwargs['screen'] = self.screen
        kwargs['connections'] = self.connections
        kwargs['device'] = self.device
        kwargs['username'] = config.username
        kwargs['password'] = config.password
        kwargs['start_time'] = time.time()
        kwargs['all_diffs'] = {}
        kwargs['diff_fh'] = mock.MagicMock()

        # We need a configlet instance too for JunoscriptRPC
        self.configlet = mock.MagicMock()
        self.configlet.name = 'configlet1'
        self.configlet.escaped_text = 'abc'
        self.mock_JunoscriptRpc = push.JunoscriptRpc(**kwargs)
        # Initialize finite state machine and transition it to
        # correct state
        self.mock_JunoscriptRpc.fsm = \
            main.FSM_PushJnx(init_state=main.FSM_PushJnx.ST_START)

        # Initialize JunosciptSslClient
        self.mock_JunoscriptRpc.proto_instance = \
            mock.create_autospec(push.JunoscriptSslClient)

        # Since this class only checks JunoscriptRpc and not
        # JunoscriptSslClint, we need to transition the statemachine
        # to next correct state i.e. the state that will be used
        # by the JunoscriptRpc method and check against it. This
        # also means we can't really check the state transtions
        # which occur due to sendRpc metod of JunoscriptSsl client
        # which occur due to calling of dataReceived method which
        # is called asynchronously

        push.os = mock.MagicMock()
        push.os.join = mock.MagicMock()
        push.os.join.path = mock.MagicMock()
        mock_open.write = mock.MagicMock()
        # We also need a separate instance of JunoscriptRpc when
        # config.xml is set as the __init__ has code which is
        # called specifically when the confi.xml is set hence
        # declaring an instance of it.
        config.xml = True

        self.mock_JunoscriptRpcxml = push.JunoscriptRpc(**kwargs)
        self.mock_JunoscriptRpcxml.proto_instance = \
            mock.create_autospec(push.JunoscriptSslClient)
        self.mock_JunoscriptRpcxml.fsm = \
            main.FSM_PushJnx(init_state=main.FSM_PushJnx.ST_START)

        config.xml = False

    @mock.patch('fbpush.push.logger.info')
    @mock.patch.object(push.JunoscriptRpc, 'updateStatus')
    def test_startedConnecting(self, mock_updateStatus, mock_logger):
        """Check the method startedConnecting."""
        self.mock_JunoscriptRpc.startedConnecting('connector1')
        msg = "Connecting to %s. (Already %d active workers)"
        mock_logger.assert_called_with(msg, self.device.name, 0)
        mock_updateStatus.assert_called_with('c', 'CONNECTING', False)

    @mock.patch('fbpush.push.logger.warn')
    @mock.patch.object(push.JunoscriptRpc, '_handle_connection_close')
    def test_clientConnectionFailed(self, mock_handler, mock_logger):
        """Check the method clientConnectionFailed."""
        reason = 'abc'
        self.mock_JunoscriptRpc.clientConnectionFailed('connector1', reason)
        msg = "Connection failed to %s %s"
        mock_logger.assert_called_with(msg, self.device.name, reason)
        mock_handler.assert_called_with()

    @mock.patch('fbpush.push.logger.info')
    @mock.patch.object(push.JunoscriptRpc, '_handle_connection_close')
    def test_clientConnectionLost(self, mock_handler, mock_logger):
        """Check the clientCOnnectionLost method."""
        reason = 'abc'
        self.mock_JunoscriptRpc.clientConnectionLost('connector1', reason)
        msg = "Connection lost to %s %s"
        mock_logger.assert_called_with(msg, self.device.name, reason)
        mock_handler.assert_called_with()

    @mock.patch('fbpush.push.logger.info')
    @mock.patch('fbpush.push.open', create=True)
    def test_sendPreamble(self, mock_open, mock_logger):
        """Check the sendPreamble method."""
        self.mock_JunoscriptRpc.sendPreamble()
        param = self.mock_JunoscriptRpc.protocol.preamble
        method = self.mock_JunoscriptRpc.proto_instance.transport.write
        method.assert_called_with(param)

        # If config.xml flag is set xml_fh file handler must be
        # to write preamble to xml file
        config.xml = True
        self.mock_JunoscriptRpcxml.sendPreamble()
        self.assertEqual(self.mock_JunoscriptRpcxml.xml_fh.write.call_count, 5)
        config.xml = False

        # After the call to sendPreamble method the state of fsm
        # should be ST_PREAMBLE_SENT
        self.assertEqual(self.mock_JunoscriptRpc.fsm.state,
                         self.mock_JunoscriptRpc.fsm.ST_PREAMBLE_SENT)

    def send_xml_call(self, mock_logger, u1, u2, u3, rpc1, *args, **kwargs):
        """This contains all the methods called after 'sending xml'.
           This is a generic method made by aggregating all the calls
           made during sending xml to network devices.

           params
           mock_logger : a mocked instances of logging method called
           u1, u2, u3 : attributes the unpdateStatus method is called with
           rpc1 : the message sendRpc is called with
           *args : the variable number of arguments the mock_logger maybe
                   called with.
            **kwargs : the optional dictionary with variable arguments
                       sendRpc maybe called with
        """
        mock_logger.assert_called_with(*args)
        self.mock_JunoscriptRpc.updateStatus.assert_called_with(u1, u2, u3)
        self.mock_JunoscriptRpc.proto_instance.sendRpc.\
            assert_called_with(rpc1, **kwargs)

    def success_reply_xml(self, u1, u2, u3, d1, success_flag, prev_state):
        """
            A method which checks all the calls made after 'receiving xml'
            and receive response is successful.
            This method aggregates all calls made after a successful session
            with network device. It also checks the whether the fsm transitions
            to correct state after that using the previcous state. Note that
            the prev_state is set statically as all the unittest currently
            are atomic in nature
            params
            u1, u2, u3 : attributes the updateStatus method is called with
            d1
            success_flag : EV_TEST_SUCCES flag
            prev_state : the previous the fsm is supposed to be in before the
                         current method is called.
        """
        main.reactor.callLater = mock.MagicMock()

        # Calculate the expected state from prev_state and success_flag
        expected_state = self.mock_JunoscriptRpc.fsm.\
            transitions[prev_state, success_flag]

        self.mock_JunoscriptRpc.updateStatus.\
            assert_called_with(u1, u2, u3)

        # Match against the expected_state and the state fsm has
        # transitioned to
        self.assertEqual(expected_state[0], self.mock_JunoscriptRpc.fsm.state)

    def failure_reply_xml(self, obj, failure_flag, d1, msg, d3, prev_state,
                          debug_msg=None, debug_args=None, mock_info=None,
                          mock_debug=None, *args):
        """
            This checks all the calls made after 'receiving xml'
            and the receive response is failure.
            This method is called only after the failure received
            in the response from network devices. It also checks
            the state the fsm transitions to by using the previous state
            params
            failure_flag : EV_TEST_FAILURE flag
            d1, msg, d3 : the arguments the updateStatus message is called with
            debug_msg : the essage with which the logger.dbug would be called
            debug_args : the arugments for debug msg
            mock_info : a mocked instance of logger.info
            mock_debug : a mocked instance of logger.debug
            prev_state : the prev state the fsm is supposed to be before the
                         method is called
            *args : the variable number of arguments with which logger.info
                    would be called
        """
        # Mock the reactor as we don;t want to make an actual call
        main.reactor.callLater = mock.MagicMock()

        # Calculate the expected state of fsm using prev_state and
        # status flag
        expected_state = self.mock_JunoscriptRpc.fsm.\
            transitions[prev_state, failure_flag]

        self.mock_JunoscriptRpc.updateStatus.\
            assert_called_with(d1, msg, d3)
        if mock_info:
            mock_info.assert_any_call(*args)
        if mock_debug:
            mock_debug(debug_msg, debug_args)

        # Check if the calculated state and state the fsm actually transitions
        # into are equal
        self.assertEqual(expected_state[0], self.mock_JunoscriptRpc.fsm.state)

    def read_xml_file(self, filename):
        """
            A method to return an xml file content.
        """
        tests_dir = os.getcwd()
        xml_file = tests_dir + '/tests/' + filename
        xml_string = open(xml_file, 'r').read()
        return xml.dom.minidom.parseString(xml_string)

    @mock.patch('fbpush.push.logger.info')
    @mock.patch('fbpush.push.logger.debug')
    @mock.patch('fbpush.main.logger.info')
    @mock.patch('fbpush.main.reactor.callLater')
    def test_success_authentication(self, mock_reactor, mock_main_log,
                                    mock_debug, mock_info):
        """ This checks the success authentication call which includes
            both sending xml and receiving xml.
        """
        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()
        self.mock_JunoscriptRpc.authenticate()
        msg = "Authenticating on device %s with username %s"
        kwargs = {}
        kwargs['username'] = config.username
        kwargs['challenge_response'] = config.password

        # Now call send_xml_call ethod which contains checks for the
        # sending xml requests to the network devices.
        self.send_xml_call(mock_info, 'a', 'AUTHENTICATING', True,
                           'request_login', msg, self.device.name,
                           config.username, **kwargs)

        auth_response = self.read_xml_file('authentication_response.xml')

        # Setting the state to the one where the fsm would have been
        # in the normal workflow of the code. In this case the method
        # testAuthenticated would be called only after 'authenticate'
        # method which sets the state to ST_AUTH_TESTING.
        EV_TEST_SUCCESS = self.mock_JunoscriptRpc.fsm.EV_TEST_SUCCESS
        jnx = self.mock_JunoscriptRpc

        jnx.fsm.state = jnx.fsm.ST_AUTH_TESTING
        prev_state = jnx.fsm.state

        # This part checks the code which is called when a success
        # message is receeived from the network devices.
        self.mock_JunoscriptRpc.testAuthenticated(auth_response)

        self.success_reply_xml(None, 'AUTHENTICATED', True,
                               jnx, EV_TEST_SUCCESS,
                               prev_state)

    @mock.patch('fbpush.push.logger.info')
    @mock.patch('fbpush.push.logger.debug')
    @mock.patch('fbpush.main.logger.info')
    @mock.patch('fbpush.main.reactor.callLater')
    def test_failure_authentication(self, mock_reactor, mock_main_log,
                                    mock_debug, mock_info):
        """ This checks the failure authentication call which includes
            both sending xml and receiving xml.
        """
        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()
        self.mock_JunoscriptRpc.authenticate()
        msg = "Authenticating on device %s with username %s"
        kwargs = {}
        kwargs['username'] = config.username
        kwargs['challenge_response'] = config.password

        # Now call send_xml_call ethod which contains checks for the
        # sending xml requests to the network devices.
        self.send_xml_call(mock_info, 'a', 'AUTHENTICATING', True,
                           'request_login', msg, self.device.name,
                           config.username, **kwargs)

        jnx = self.mock_JunoscriptRpc
        # Now check the part which should be called in case of a failure
        # message
        auth_failure_file = 'authentication_response_failure.xml'
        auth_response = self.read_xml_file(auth_failure_file)

        jnx.fsm.state = jnx.fsm.ST_AUTH_TESTING
        prev_state = jnx.fsm.state
        EV_TEST_FAILURE = self.mock_JunoscriptRpc.fsm.EV_TEST_FAILURE

        self.mock_JunoscriptRpc.testAuthenticated(auth_response)

        status = 'failure'
        msg = 'abcd'
        log_info = 'Authentication on %s failed with result %s:%s'
        log_debug = 'RPC reply:\n%s'
        update_msg = 'AUTHENTICATION: %s:%s'
        EV_TEST_FAILURE = self.mock_JunoscriptRpc.fsm.EV_TEST_FAILURE
        umsg = update_msg % (status, msg)
        self.failure_reply_xml(jnx, EV_TEST_FAILURE, None, umsg, False,
                               prev_state,
                               log_debug, auth_response.toprettyxml(),
                               mock_info, mock_debug, log_info,
                               self.device.name, status, msg)

    @mock.patch('fbpush.push.logger.info')
    @mock.patch('fbpush.push.logger.debug')
    def test_success_locking(self, mock_debug, mock_info):
        """ This checks the success locking call which includes
            both sending xml and receiving xml.
        """
        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()
        self.mock_JunoscriptRpc.lockConfiguration()
        msg = 'Locking configuration on device %s'
        kwargs = {}

        # Now call send_xml_call ethod which contains checks for the
        # sending xml requests to the network devices.
        self.send_xml_call(mock_info, 'l', 'ACQUIRING CONFIG LOCK', True,
                           'lock_configuration', msg, self.device.name,
                           **kwargs)

        lock_response = self.read_xml_file('locking_response.xml')
        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()

        # Setting the state to the one where the fsm would have been
        # in the normal workflow of the code. In this case the method
        # testLocked would be called only after 'lockConfiguration'
        # method which sets the state to ST_CONFIG_LOCK_TESTING.
        # This prev_state is commond for both success and failure.
        jnx = self.mock_JunoscriptRpc

        jnx.fsm.state = jnx.fsm.ST_CONFIG_LOCK_TESTING
        prev_state = jnx.fsm.state

        # Testing the code when the xml_reply is successful
        EV_TEST_SUCCESS = self.mock_JunoscriptRpc.fsm.EV_TEST_SUCCESS

        self.mock_JunoscriptRpc.testLocked(lock_response)
        self.success_reply_xml(None, 'LOCK ACQUIRED', True,
                               jnx, EV_TEST_SUCCESS,
                               prev_state)

    @mock.patch('fbpush.push.logger.info')
    @mock.patch('fbpush.push.logger.debug')
    def test_failure_locking(self, mock_debug, mock_info):
        """ This checks the failure locking call which includes
            both sending xml and receiving xml.
        """

        jnx = self.mock_JunoscriptRpc

        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()
        self.mock_JunoscriptRpc.lockConfiguration()
        msg = 'Locking configuration on device %s'
        kwargs = {}

        # Now call send_xml_call ethod which contains checks for the
        # sending xml requests to the network devices.
        self.send_xml_call(mock_info, 'l', 'ACQUIRING CONFIG LOCK', True,
                           'lock_configuration', msg, self.device.name,
                           **kwargs)
        # Now testing the code when the xml reply is failure
        lock_response = self.read_xml_file('locking_response_failure.xml')

        jnx.fsm.state = jnx.fsm.ST_CONFIG_LOCK_TESTING
        prev_state = jnx.fsm.state
        EV_TEST_FAILURE = self.mock_JunoscriptRpc.fsm.EV_TEST_FAILURE

        self.mock_JunoscriptRpc.testLocked(lock_response)
        msg = 'locking_failure'
        log_info = 'Lock configuration on %s failed with message: %s'
        log_debug = 'RPC reply:\n%s'
        update_msg = 'Locking Failed: %s'
        umsg = update_msg % msg

        self.failure_reply_xml(jnx, EV_TEST_FAILURE, None, umsg, False,
                               prev_state,
                               log_debug, lock_response.toprettyxml(),
                               mock_info, mock_debug, log_info,
                               self.device.name, msg)

    @mock.patch('fbpush.push.logger.info')
    def test_getConfiguration(self, mock_logger):
        """ This checks getConfiguration method. """
        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()
        self.mock_JunoscriptRpc.getConfiguration()
        msg = 'Retrieving configuration from device %s'
        kwargs = {}
        kwargs['compare'] = 'rollback'
        kwargs['rollback'] = '0'

        # Now call send_xml_call method which contains checks for the
        # sending xml requests to the network devices.
        self.send_xml_call(mock_logger, 'r', 'READING CONFIGURATION', True,
                           'get_configuration', msg, self.device.name,
                           **kwargs)

    @mock.patch('fbpush.push.logger.info')
    @mock.patch('fbpush.push.logger.debug')
    @mock.patch('fbpush.main.logger.info')
    @mock.patch('fbpush.main.reactor.callLater')
    @mock.patch.object(push.JunoscriptRpc, 'getNextConfiglet')
    def test_success_config_upload(self, mock_getNextConfiglet, mock_reactor,
                                   mock_main_log, mock_debug, mock_info):
        """ This checks the success config upload call which includes
             both sending xml and receiving xml.
        """
        mock_getNextConfiglet.return_value = self.configlet
        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()
        self.mock_JunoscriptRpc.uploadConfiguration()
        msg = 'Uploading configlet %s on device %s'
        kwargs = {}
        kwargs['format'] = 'text'
        kwargs['action'] = 'replace'
        kwargs['configuration_text'] = self.configlet.escaped_text
        umsg = u'UPLOADING CONFIGLET %s' % self.configlet.name

        # Now call send_xml_call method which contains checks for the
        # sending xml requests to the network devices.
        self.send_xml_call(mock_info, 'u', umsg, True,
                           'load_configuration', msg, self.configlet.name,
                           self.device.name, **kwargs)

        lock_response = self.read_xml_file('uploaded_configuration.xml')
        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()

        # Setting the state to the one where the fsm would have been
        # in the normal workflow of the code. In this case the method
        # testConfigUpload would be called only after 'configUpload'
        # method which sets the state to ST_CONFIG_LOCK_TESTING.
        # This prev_state is common for both success and failure.
        jnx = self.mock_JunoscriptRpc

        jnx.fsm.state = jnx.fsm.ST_CONFIG_LOCK_TESTING
        prev_state = jnx.fsm.state

        # Testing the code when the xml_reply is successful
        EV_TEST_SUCCESS = self.mock_JunoscriptRpc.fsm.EV_TEST_SUCCESS

        self.mock_JunoscriptRpc.testConfigUpload(lock_response)
        self.success_reply_xml(None, 'CONFIGLET UPLOADED', True,
                               jnx, EV_TEST_SUCCESS,
                               prev_state)

    @mock.patch('fbpush.push.logger.info')
    @mock.patch('fbpush.push.logger.debug')
    @mock.patch('fbpush.main.logger.info')
    @mock.patch('fbpush.main.reactor.callLater')
    @mock.patch.object(push.JunoscriptRpc, 'getNextConfiglet')
    def test_failure_config_upload(self, mock_getNextConfiglet, mock_reactor,
                                   mock_main_log, mock_debug, mock_info):
        """ This checks the failure config upload call which includes
             both sending xml and receiving xml.
        """
        mock_getNextConfiglet.return_value = self.configlet
        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()
        self.mock_JunoscriptRpc.uploadConfiguration()
        msg = 'Uploading configlet %s on device %s'
        kwargs = {}
        kwargs['format'] = 'text'
        kwargs['action'] = 'replace'
        kwargs['configuration_text'] = self.configlet.escaped_text
        umsg = u'UPLOADING CONFIGLET %s' % self.configlet.name

        jnx = self.mock_JunoscriptRpc
        # Now call send_xml_call method which contains checks for the
        # sending xml requests to the network devices.
        self.send_xml_call(mock_info, 'u', umsg, True,
                           'load_configuration', msg, self.configlet.name,
                           self.device.name, **kwargs)

        # Now testing the code when the xml reply is failure
        lock_response = self.\
            read_xml_file('uploaded_configuration_failure.xml')

        jnx.fsm.state = jnx.fsm.ST_CONFIG_LOCK_TESTING
        prev_state = jnx.fsm.state
        EV_TEST_FAILURE = self.mock_JunoscriptRpc.fsm.EV_TEST_FAILURE

        self.mock_JunoscriptRpc.testConfigUpload(lock_response)
        msg = 'upload_failure'
        log_info = 'Configlet upload on %s failed with message: %s'
        log_debug = 'RPC reply:\n%s'
        update_msg = 'Upload Failed: %s'
        umsg = update_msg % msg

        self.failure_reply_xml(jnx, EV_TEST_FAILURE, None, umsg, False,
                               prev_state,
                               log_debug, lock_response.toprettyxml(),
                               mock_info, mock_debug, log_info,
                               self.device.name, msg)

    @mock.patch('fbpush.push.logger.info')
    @mock.patch('fbpush.push.logger.debug')
    @mock.patch('fbpush.main.logger.info')
    @mock.patch('fbpush.main.reactor.callLater')
    def test_success_commit_check(self, mock_reactor, mock_main_log,
                                  mock_debug, mock_info):
        """ This checks the successful commitCheck method call
            both sending and receiving xml
        """
        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()
        self.mock_JunoscriptRpc.commitCheck()
        msg = 'Executing commit check on device %s'
        kwargs = {}
        kwargs['check'] = True

        # Now call send_xml_call method which contains checks for the
        # sending xml requests to the network devices.
        self.send_xml_call(mock_info, 'h', 'EXECUTING COMMIT CHECK', True,
                           'commit_configuration', msg, self.device.name,
                           **kwargs)

        commit_cresponse = self.read_xml_file('commited_check_reply.xml')
        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()

        # Setting the state to the one where the fsm would have been
        # in the normal workflow of the code. In this case the method
        # testCommitCheck would be called only after 'commitCheck'
        # method which sets the state to ST_COMMIT_CHECK_TESTING.
        # This prev_state is common for both success and failure.
        jnx = self.mock_JunoscriptRpc

        jnx.fsm.state = jnx.fsm.ST_COMMIT_CHECK_TESTING
        prev_state = jnx.fsm.state

        # Testing the code when the xml_reply is successful
        EV_TEST_SUCCESS = self.mock_JunoscriptRpc.fsm.EV_TEST_SUCCESS

        self.mock_JunoscriptRpc.testCommitCheck(commit_cresponse)
        self.success_reply_xml(None, u'COMMIT CHECKED', True,
                               jnx, EV_TEST_SUCCESS,
                               prev_state)

    @mock.patch('fbpush.push.logger.info')
    @mock.patch('fbpush.push.logger.debug')
    @mock.patch('fbpush.main.logger.info')
    @mock.patch('fbpush.main.reactor.callLater')
    def test_failure_commit_check(self, mock_reactor, mock_main_log,
                                  mock_debug, mock_info):
        """ This checks the failure commitCheck method call
            both sending and receiving xml
        """
        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()
        self.mock_JunoscriptRpc.commitCheck()
        msg = 'Executing commit check on device %s'
        kwargs = {}
        kwargs['check'] = True

        # Now call send_xml_call method which contains checks for the
        # sending xml requests to the network devices.
        self.send_xml_call(mock_info, 'h', 'EXECUTING COMMIT CHECK', True,
                           'commit_configuration', msg, self.device.name,
                           **kwargs)

        jnx = self.mock_JunoscriptRpc
        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()
        # Now testing the code when the xml reply is failure
        commit_cresponse = self.\
            read_xml_file('commited_check_reply_success_w_errors.xml')

        jnx.fsm.state = jnx.fsm.ST_COMMIT_CHECK_TESTING
        prev_state = jnx.fsm.state
        EV_TEST_SUCCESS = self.mock_JunoscriptRpc.fsm.EV_TEST_SUCCESS

        self.mock_JunoscriptRpc.testCommitCheck(commit_cresponse)
        msg = 'commit_check_failure'
        log_info = 'Commit check succeeded on %s with message: %s'
        log_debug = 'RPC reply:\n%s'
        update_msg = 'COMMIT CHECKED WITH ERROR(S): %s'
        umsg = update_msg % msg

        self.failure_reply_xml(jnx, EV_TEST_SUCCESS, None, umsg, True,
                               prev_state,
                               log_debug, commit_cresponse.toprettyxml(),
                               mock_info, mock_debug, log_info,
                               self.device.name, msg)

    @mock.patch('fbpush.main.logger.info')
    @mock.patch('fbpush.main.reactor.callLater')
    def test_diffConfiguration(self, mock_call_later, mock_info):
        """ This checks the diffConfiguration method."""
        diff_configuration = self.read_xml_file('diff_configuration.xml')
        jnx = self.mock_JunoscriptRpc
        jnx.updateStatus = mock.MagicMock()
        jnx.diff_fh.write = mock.MagicMock()

        # set config.multiple_diffs True too so as to check the call to
        # self.diff_fh file handler
        config.multiple_diffs = True

        # before calling of diffConfiguration of file handler the
        # fsm should be ST_CONFIG_DIFFING state
        jnx.fsm.state = jnx.fsm.ST_CONFIG_DIFFING
        prev_state = jnx.fsm.state

        jnx.diffConfiguration(diff_configuration)
        # Check whether the calls made to updateStatus and diff_fh file
        # handler are correct
        jnx.updateStatus.assert_called_with(None, 'DIFF SAVED', True)
        self.assertEqual(jnx.diff_fh.write.call_count, 1)

        # Calculate the expected state from the transitions dictornary
        expected_state = jnx.fsm.transitions[prev_state, jnx.fsm.EV_NEXT]

        # Check the current state of fsm against calculated state
        self.assertEqual(expected_state[0], jnx.fsm.state)

    @mock.patch('fbpush.push.logger.info')
    @mock.patch('fbpush.push.logger.debug')
    @mock.patch('fbpush.main.logger.info')
    @mock.patch('fbpush.main.reactor.callLater')
    def test_success_real_commit(self, mock_reactor, mock_main_log,
                                 mock_debug, mock_info):
        """ This checks the successful checkRealCommi call
            both success and failure
        """

        jnx = self.mock_JunoscriptRpc
        jnx.configlet_names = 'abc'
        jnx.configlet_hash = mock.MagicMock()
        jnx.configlet_hash.hexdigest = mock.MagicMock()

        jnx.configlet_hash.hexdigest.return_value = 'hex1'

        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()
        self.mock_JunoscriptRpc.commitForReal()
        msg = 'Executing commit on device %s'
        kwargs = {}
        kwargs = {
            'log': "MD5(%s)=%s" % (','.join(jnx.configlet_names),
                                   jnx.configlet_hash.hexdigest()),
            'full': True
        }

        # Now call send_xml_call ethod which contains checks for the
        # sending xml requests to the network devices.
        self.send_xml_call(mock_info, '.', 'EXECUTING REAL COMMIT', True,
                           'commit_configuration', msg, self.device.name,
                           **kwargs)
        rcommit_response = self.read_xml_file('real_commit.xml')
        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()

        # Setting the state to the one where the fsm would have been
        # in the normal workflow of the code. In this case the method
        # testRealCommit would be called only after 'commitForReal'
        # method which sets the state to ST_COMMIT_FOR_REAL_TESTING.
        # This prev_state is commond for both success and failure.
        jnx = self.mock_JunoscriptRpc

        jnx.fsm.state = jnx.fsm.ST_COMMIT_FOR_REAL_TESTING
        prev_state = jnx.fsm.state

        # Testing the code when the xml_reply is successful
        EV_TEST_SUCCESS = self.mock_JunoscriptRpc.fsm.EV_TEST_SUCCESS

        self.mock_JunoscriptRpc.testRealCommit(rcommit_response)
        self.success_reply_xml('!', 'COMMITTED SUCCESSFULLY!', False,
                               jnx, EV_TEST_SUCCESS, prev_state)

    @mock.patch('fbpush.push.logger.info')
    @mock.patch('fbpush.push.logger.debug')
    @mock.patch('fbpush.main.logger.info')
    @mock.patch('fbpush.main.reactor.callLater')
    def test_failure_real_commit(self, mock_reactor, mock_main_log,
                                 mock_debug, mock_info):
        """ This checks the failure checkRealCommi call
            both success and failure
        """

        jnx = self.mock_JunoscriptRpc
        jnx.configlet_names = 'abc'
        jnx.configlet_hash = mock.MagicMock()
        jnx.configlet_hash.hexdigest = mock.MagicMock()

        jnx.configlet_hash.hexdigest.return_value = 'hex1'

        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()
        self.mock_JunoscriptRpc.commitForReal()
        msg = 'Executing commit on device %s'
        kwargs = {}
        kwargs = {
            'log': "MD5(%s)=%s" % (','.join(jnx.configlet_names),
                                   jnx.configlet_hash.hexdigest()),
            'full': True
        }

        # Now call send_xml_call ethod which contains checks for the
        # sending xml requests to the network devices.
        self.send_xml_call(mock_info, '.', 'EXECUTING REAL COMMIT', True,
                           'commit_configuration', msg, self.device.name,
                           **kwargs)

        # Now testing the code when the xml reply is failure
        rcommit_response = self.read_xml_file('real_commit_failure.xml')

        jnx.fsm.state = jnx.fsm.ST_COMMIT_FOR_REAL_TESTING
        prev_state = jnx.fsm.state
        EV_TEST_FAILURE = self.mock_JunoscriptRpc.fsm.EV_TEST_FAILURE

        self.mock_JunoscriptRpc.testRealCommit(rcommit_response)
        msg = 'commit_check_failure'
        log_info = 'Commit on %s failed with message: %s'
        log_debug = 'RPC reply:\n%s'
        update_msg = 'Commit Failed: %s'
        umsg = update_msg % msg

        self.failure_reply_xml(jnx, EV_TEST_FAILURE, None, umsg, False,
                               prev_state,
                               log_debug, rcommit_response.toprettyxml(),
                               mock_info, mock_debug, log_info,
                               self.device.name, msg)

    @mock.patch('fbpush.push.logger.info')
    @mock.patch('fbpush.push.logger.debug')
    @mock.patch('fbpush.main.logger.info')
    @mock.patch('fbpush.main.reactor.callLater')
    def test_success_commit_confirmed(self, mock_reactor, mock_main_log,
                                      mock_debug, mock_info):
        """ This checks successful commitConfirmed method which
            includes both sending and receiving xml
        """
        jnx = self.mock_JunoscriptRpc
        jnx.configlet_names = 'abc'
        jnx.configlet_hash = mock.MagicMock()
        jnx.configlet_hash.hexdigest = mock.MagicMock()

        jnx.configlet_hash.hexdigest.return_value = 'hex1'

        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()
        self.mock_JunoscriptRpc.commitConfirmed()
        msg = 'Executing commit confirmed on device %s'
        kwargs = {
            'log': "MD5(%s)=%s" % (','.join(jnx.configlet_names),
                                   jnx.configlet_hash.hexdigest()),
            'confirmed': True,
            'confirm_timeout': config.rollback_in
        }

        # Now call send_xml_call ethod which contains checks for the
        # sending xml requests to the network devices.
        self.send_xml_call(mock_info, '.', 'EXECUTING COMMIT CONFIRMED',
                           True,
                           'commit_configuration', msg, self.device.name,
                           **kwargs)

        cconfirmed_response = self.read_xml_file('commit_confirmed.xml')
        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()

        # Setting the state to the one where the fsm would have been
        # in the normal workflow of the code. In this case the method
        # testCommitConfirmed would be called only after 'commitConfirmed'
        # method which sets the state to ST_COMMIT_CONFIRMED_TESTING.
        # This prev_state is common for both success and failure.
        jnx = self.mock_JunoscriptRpc

        jnx.fsm.state = jnx.fsm.ST_COMMIT_CONFIRMED_TESTING
        prev_state = jnx.fsm.state

        # Testing the code when the xml_reply is successful
        EV_TEST_SUCCESS = self.mock_JunoscriptRpc.fsm.EV_TEST_SUCCESS
        config.rollback_in = 140000
        self.mock_JunoscriptRpc.testCommitConfirmed(cconfirmed_response)
        self.success_reply_xml('!', 'COMMITTED CONFIRMED SUCCESSFULLY!', False,
                               jnx, EV_TEST_SUCCESS,
                               prev_state)

    @mock.patch('fbpush.push.logger.info')
    @mock.patch('fbpush.push.logger.debug')
    @mock.patch('fbpush.main.logger.info')
    @mock.patch('fbpush.main.reactor.callLater')
    def test_failure_commit_confirmed(self, mock_reactor, mock_main_log,
                                      mock_debug, mock_info):
        """ This checks failure commitConfirmed method which
            includes both sending and receiving xml
        """
        jnx = self.mock_JunoscriptRpc
        jnx.configlet_names = 'abc'
        jnx.configlet_hash = mock.MagicMock()
        jnx.configlet_hash.hexdigest = mock.MagicMock()

        jnx.configlet_hash.hexdigest.return_value = 'hex1'

        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()
        self.mock_JunoscriptRpc.commitConfirmed()
        msg = 'Executing commit confirmed on device %s'
        kwargs = {
            'log': "MD5(%s)=%s" % (','.join(jnx.configlet_names),
                                   jnx.configlet_hash.hexdigest()),
            'confirmed': True,
            'confirm_timeout': config.rollback_in
        }

        # Now call send_xml_call ethod which contains checks for the
        # sending xml requests to the network devices.
        self.send_xml_call(mock_info, '.', 'EXECUTING COMMIT CONFIRMED',
                           True,
                           'commit_configuration', msg, self.device.name,
                           **kwargs)

        # Now testing the code when the xml reply is failure
        lock_response = self.\
            read_xml_file('commit_confirmed_failure.xml')

        jnx.fsm.state = jnx.fsm.ST_COMMIT_CONFIRMED_TESTING
        prev_state = jnx.fsm.state
        EV_TEST_FAILURE = self.mock_JunoscriptRpc.fsm.EV_TEST_FAILURE

        self.mock_JunoscriptRpc.testCommitConfirmed(lock_response)
        msg = 'commit_confirmed_failure'
        log_info = 'Commit on %s failed with message: %s'
        log_debug = 'RPC reply:\n%s'
        update_msg = 'Commit Confirmed Failed: %s'
        umsg = update_msg % msg

        self.failure_reply_xml(jnx, EV_TEST_FAILURE, None, umsg, False,
                               prev_state,
                               log_debug, lock_response.toprettyxml(),
                               mock_info, mock_debug, log_info,
                               self.device.name, msg)

    @mock.patch('fbpush.main.logger.info')
    @mock.patch('fbpush.main.reactor.callLater')
    def test_waitForConfirm(self, mock_callLater, mock_info):
        jnx = self.mock_JunoscriptRpc

        jnx.fsm.state = jnx.fsm.ST_WAIT_FOR_CONFIRM
        prev_state = jnx.fsm.state

        # The case when there is a commit_timer not set
        jnx.updateStatus = mock.MagicMock()

        jnx.commit_timer = 1

        jnx.progress_chr = mock.MagicMock()
        jnx.progress_chr.next = mock.MagicMock()
        jnx.progress_chr.next.return_value = 'abc'

        kwargs = {}
        msg = 'AUTOMATIC COMMIT IN %d SECONDS' % jnx.commit_timer
        kwargs['error_msg'] = msg
        kwargs['completed'] = False
        kwargs['spin'] = True
        jnx.waitForConfirm()

        jnx.updateStatus.assert_called_with('abc', **kwargs)
        expected_state = jnx.fsm.transitions[prev_state, jnx.fsm.EV_CONFIRM]

        self.assertEqual(expected_state[0], jnx.fsm.state)

    @mock.patch('fbpush.push.logger.info')
    @mock.patch('fbpush.push.logger.debug')
    @mock.patch('fbpush.main.logger.info')
    @mock.patch('fbpush.main.reactor.callLater')
    def test_success_testRollBack(self, mock_reactor, mock_main_log,
                                  mock_debug, mock_info):

        rollback_response = self.read_xml_file('rollback_response.xml')
        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()

        # Checking when rollback is successful
        jnx = self.mock_JunoscriptRpc

        jnx.fsm.state = jnx.fsm.ST_ROLLBACK_TESTING
        prev_state = jnx.fsm.state

        # Testing the code when the xml_reply is successful
        EV_ROLLBACK_SUCCESS = self.mock_JunoscriptRpc.fsm.EV_ROLLBACK_SUCCESS

        self.mock_JunoscriptRpc.testRollBack(rollback_response)
        self.success_reply_xml(None, 'ROLLBACK LAST CONFIG', True,
                               jnx, EV_ROLLBACK_SUCCESS,
                               prev_state)

    @mock.patch('fbpush.push.logger.info')
    @mock.patch('fbpush.push.logger.debug')
    @mock.patch('fbpush.main.logger.info')
    @mock.patch('fbpush.main.reactor.callLater')
    def test_failure_testRollBack(self, mock_reactor, mock_main_log,
                                  mock_debug, mock_info):

        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()
        jnx = self.mock_JunoscriptRpc

        # Now checking when the xm_reply is failure

        rollback_response = self.read_xml_file('rollback_response_failure.xml')
        jnx.fsm.state = jnx.fsm.ST_ROLLBACK_TESTING
        prev_state = jnx.fsm.state

        EV_ROLLBACK_FAIL = self.mock_JunoscriptRpc.fsm.EV_ROLLBACK_FAIL

        self.mock_JunoscriptRpc.testRollBack(rollback_response)

        self.failure_reply_xml(jnx, EV_ROLLBACK_FAIL, None, 'ROLLBACK FAIL',
                               True, prev_state)

    @mock.patch('fbpush.push.logger.info')
    @mock.patch('fbpush.push.logger.debug')
    @mock.patch('fbpush.main.logger.info')
    @mock.patch('fbpush.main.reactor.callLater')
    def test_success_testRollBackCommit(self, mock_reactor, mock_main_log,
                                        mock_debug, mock_info):

        rollback_response = self.read_xml_file('roll_back.xml')
        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()

        jnx = self.mock_JunoscriptRpc

        jnx.fsm.state = jnx.fsm.ST_ROLLBACK_COMMIT_TESTING
        prev_state = jnx.fsm.state
        # Testing the code when the xml_reply is successful
        EV_ROLLBACK_COMMIT_SUCCESS = jnx.fsm.EV_ROLLBACK_COMMIT_SUCCESS

        jnx.testRollBackCommit(rollback_response)
        self.success_reply_xml(None, 'ROLLBACK COMMIT SUCCESSFULLY', True,
                               jnx, EV_ROLLBACK_COMMIT_SUCCESS,
                               prev_state)

    @mock.patch('fbpush.push.logger.info')
    @mock.patch('fbpush.push.logger.debug')
    @mock.patch('fbpush.main.logger.info')
    @mock.patch('fbpush.main.reactor.callLater')
    def test_failure_testRollBackCommit(self, mock_reactor, mock_main_log,
                                        mock_debug, mock_info):

        jnx = self.mock_JunoscriptRpc

        self.mock_JunoscriptRpc.updateStatus = mock.MagicMock()

        # Now testing the code when the xml reply is failure
        rollback_response = self.\
            read_xml_file('roll_back_failure.xml')

        jnx.fsm.state = jnx.fsm.ST_ROLLBACK_COMMIT_TESTING
        prev_state = jnx.fsm.state
        EV_ROLLBACK_COMMIT_FAIL = jnx.fsm.EV_ROLLBACK_COMMIT_FAIL

        jnx.testRollBackCommit(rollback_response)
        msg = 'roll_back_failure'

        umsg = 'ROLLBACK COMMIT FAIL: %s' % msg

        self.failure_reply_xml(jnx, EV_ROLLBACK_COMMIT_FAIL, None, umsg,
                               True, prev_state)


if __name__ == '__main__':
    unittest.main()
