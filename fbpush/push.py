# Copyright (c) 2015-present, Facebook, Inc.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree. An additional grant
# of patent rights can be found in the PATENTS file in the same directory.
#


"""Push script for Juniper devices.
"""

import collections
import curses
import hashlib
import itertools
import logging
import os
import re
import time
from xml.dom import minidom
from xml.sax import expatreader
from xml.sax import handler
from xml.sax._exceptions import SAXParseException

from twisted.internet import reactor
from twisted.internet import defer
from twisted.internet import protocol

from . import config


logger = logging.getLogger()

HELP_TEXT_1 = """
********************** fbpush *********************

Description of the status line(s):
node.site(configlet1, configlet2, ...): <Progress> [Last result]

node.site -- router name (e.g. br01.ams1, etc.)
(configlet1, ...) -- list of eligible configlets being pushed
<Progress> -- Progress of push process, defined as:
    c: connecting
    C: Connected
    a: authenticating
    A: Authenticated
    l: aquiring configuration lock (locking)
    L: acquired configuration lock (Locked)
    r: reading configuration
    R: Read configuration
        (if diffing option was specified will happen twice)
    u: uploading configlet
    U: Uploaded configlet
        (will be present for each eligible configlet)
    d: calculating diff
    D: calculated and saved Diff
    h: executing "commit check"
    H: "commit cHeck" successful
    .: committing configuration
    !: committed configuration
[Last result] -- Verbose status or error code of last operation
"""


class CursesStdIO(object):
    """Fake FD to be registered as a reader with the twisted reactor.

       Curses classes needing input should extend this
    """

    def fileno(self):
        """We want to select on FD 0
        """
        return 0

    def doRead(self):
        """Called when input is ready
        """
        raise NotImplementedError('This method must be implemented!')

    def logPrefix(self):
        return 'CursesClient'


class Screen(CursesStdIO):

    PAGES = {
        'HELP_TEXT_1': lambda: HELP_TEXT_1.split('\n'),
    }

    ST_STATUS = 1
    ST_QUIT = 2
    ST_HELP = 3

    def __init__(self, connections, stdscr):
        self.connections = connections
        self.stdscr = stdscr
        self.rows, self.cols = self.stdscr.getmaxyx()

        # set screen attributes
        self.stdscr.nodelay(1)  # this is used to make input calls non-blocking
        curses.cbreak()
        self.stdscr.keypad(1)
        curses.curs_set(0)      # no annoying mouse cursor
        curses.start_color()
        # create color pair's 1 and 2
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(2, curses.COLOR_CYAN, curses.COLOR_BLACK)
        curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
        curses.init_pair(4, curses.COLOR_YELLOW, curses.COLOR_RED)
        self.PAGES['STATUS'] = self.statusPage
        self.PAGES['QUIT'] = self.quitPage
        self.screen_page = self.PAGES['STATUS']
        self.status_text = '%s%s%s' % (
                "'h':help,'r':retry(juniper),'q':quit,",
                "'l':rollback during commit confirm,",
                "Arrows/Page up/down:scroll")
        if config.confirmed_in:
            self.status_text += ", '!' for confirm"
        self.start_row = 0
        self.start_col = 0
        self.last_repaint = 0.0
        self.max_col = 0
        self.state = self.ST_STATUS
        self.q_clicked = 0
        self.repaint()

    def connectionLost(self, reason):
        logger.error('Screen.connectionLost() called. Reason:%s', reason)
        self.close()

    def repaint(self):
        """Repaint current screen page and bottom status line
        """
        # limit the screen refresh rate
        # otherwise if # of devices is large, the screen goes crazy
        current_time = time.time()
        if current_time - self.last_repaint < 0.05:
            return
        self.last_repaint = current_time

        logger.debug("Screen.repaint() called and starting...")
        try:
            self.stdscr.clear()
            self.stdscr.box()
            row_count = 0

            # max_col change dynamically for each refresh
            self.max_col = max([len(line) for line in self.screen_page()])

            for row, line in enumerate(self.screen_page()):
                if row < self.start_row:
                    continue

                line = '%d %s' % (row + 1, line)
                tmp = line[self.start_col:]
                line = "%s" % tmp[:self.cols - 5]

                self.stdscr.addstr(row_count + 1, 1, line)
                row_count = row_count + 1
                if row_count > (self.rows - 5):
                    break

            if self.q_clicked:
                qmsg = ''.join([
                    '"q" is clicked %d times. ' % self.q_clicked,
                    'to prevent locking the device, for iosxr conn.',
                ])
                self.stdscr.addstr(
                        self.rows - 3, 1, qmsg, curses.color_pair(3))

                qmsg = ''.join([
                    '"q" is only effective when the conn is at ',
                    'commit confirm/failed/finished state',
                ])
                self.stdscr.addstr(
                        self.rows - 2, 1, qmsg, curses.color_pair(3))
            self.stdscr.addstr(
                self.rows - 1, 1, self.status_text, curses.color_pair(3))
            self.stdscr.refresh()
        except Exception as e:
            logger.error("Caught exception %s while repainting screen!", e)
        logger.debug("... repaint done.")

    def statusPage(self):
        """Returns a list of status lines for all connections.
        """
        lines = []
        for jnx in self.connections:
            lines.append("%s(%s): %s [%s]" % (
                jnx.device.name,
                ','.join([cn.name for cn in jnx.configlets]),
                ''.join(jnx.progress_bar),
                jnx.error_msg
            ))
        return lines

    def quitPage(self):
        lines = []
        waiting_conn = self.getWaitingConnections()
        lines.extend([
            'Some devices are still in commit confirm/rollback state',
            'fbpush will rollback the last config on:', ''])
        for conn in waiting_conn:
            lines.append(conn.device.name)
        lines.extend(['',
                      'Try "q" again when rollback+commit are finished',
                      '',
                     'Are you sure to rollback? [y/n]'])
        self.state = self.ST_QUIT
        return lines

    def getWaitingConnections(self):
        '''
        get those connections where vendor == juniper and
        state == ST_WAIT_FOR_CONFIRM or rollback-ing
        '''
        ret = []
        for conn in self.connections:
            if conn.device.vendor.name == 'juniper':
                if (conn.fsm.state in [conn.fsm.ST_WAIT_FOR_CONFIRM,
                                       conn.fsm.ST_ROLLBACK,
                                       conn.fsm.ST_ROLLBACK_TESTING,
                                       conn.fsm.ST_ROLLBACK_COMMIT,
                                       conn.fsm.ST_ROLLBACK_COMMIT_TESTING]):
                    ret.append(conn)
        return ret

    def doRead(self):
        """ Input is ready! """

        curses.noecho()
        c = self.stdscr.getch()  # read a character
        logger.info("Keystroke received: %s", c)

        if c == curses.KEY_UP:
            if self.start_row + self.rows < len(self.connections) + 3:
                self.start_row = self.start_row + 1
            self.repaint()

        elif c == curses.KEY_DOWN:
            if self.start_row >= 0:
                self.start_row = self.start_row - 1
            self.repaint()

        elif c == curses.KEY_RIGHT:
            if self.start_col < self.max_col - 10:
                self.start_col = self.start_col + 1
            self.repaint()

        elif c == curses.KEY_LEFT:
            if self.start_col >= 1:
                self.start_col = self.start_col - 1
            self.repaint()

        elif c == curses.KEY_NPAGE:
            if (self.start_row + self.rows + self.rows <
                    len(self.connections) + 3):
                self.start_row = self.start_row + self.rows
            else:
                self.start_row = len(self.connections) - self.rows + 3
            self.repaint()

        elif c == curses.KEY_PPAGE:
            if self.start_row - self.rows >= 0:
                self.start_row = self.start_row - self.rows
            else:
                self.start_row = 0
            self.repaint()

        # it is not ASCII, skip it, otherwise chr(c) will fail
        elif c > 255:
            return

        elif chr(c) in ['R', 'r']:
            for conn in self.connections:
                if (conn.device.vendor.name == 'juniper' and
                        conn.fsm.state == conn.fsm.ST_WAIT_FOR_RETRY):
                    conn.fsm.dispatch(conn, conn.fsm.EV_RETRY)

        elif chr(c) in ['Q', 'q']:
            waiting_conn = self.getWaitingConnections()
            self.q_clicked += 1
            self.repaint()

            if not waiting_conn:
                # no connection is waiting, quit
                self.close()
            else:
                self.screen_page = Screen.PAGES['QUIT']
                self.repaint()

        elif chr(c) in ['L', 'l']:
            self.screen_page = Screen.PAGES['QUIT']
            self.repaint()

        elif chr(c) in ['N', 'n']:
            # to prevent people just type 'n'
            if self.state != self.ST_QUIT:
                return

            self.state = self.ST_STATUS
            self.screen_page = Screen.PAGES['STATUS']
            self.repaint()

        elif chr(c) in ['Y', 'y']:
            # to prevent people just type 'y'
            if self.state != self.ST_QUIT:
                return

            self.state = self.ST_STATUS
            self.screen_page = Screen.PAGES['STATUS']
            self.repaint()

            # rollback
            for conn in self.connections:
                # only juniper device will lock on commit confirm
                # ignore rollback-ing devices
                if (conn.fsm.state == conn.fsm.ST_WAIT_FOR_CONFIRM and
                        conn.device.vendor.name == 'juniper'):
                    conn.fsm.dispatch(conn, conn.fsm.EV_ROLLBACK)

        elif chr(c) in ['H', 'h']:
            self.state = self.ST_HELP
            self.screen_page = Screen.PAGES['HELP_TEXT_1']
            self.status_text = "Press 's' for status"
            self.repaint()

        elif chr(c) in ['S', 's']:
            self.state = self.ST_STATUS
            self.screen_page = Screen.PAGES['STATUS']
            self.status_text = "Press 'h' for help, 'r' \
                                for retry, 'q' for quit"
            if config.confirmed_in:
                self.status_text += ", '!' for confirm"
            self.repaint()

        elif chr(c) == '!':
            for conn in self.connections:
                if conn.fsm.state == conn.fsm.ST_WAIT_FOR_CONFIRM:
                    conn.fsm.dispatch(conn, conn.fsm.EV_CONFIRM)
        else:
            pass

    def _close(self, ret=None):
        try:
            reactor.stop()
        except:
            pass

    def close(self):
        logger.info('Screen.close() called')
        curses.nocbreak()
        self.stdscr.keypad(0)
        curses.echo()
        curses.endwin()

        self._close()


class SaxHandler(handler.ContentHandler):
    """
    """

    def __init__(self, client):
        self.client = client
        self.tag_stack = []

    def startDocument(self):
        """Event received at the beginning of XML document.
        """
        logger.debug('startDocument received')

    def endDocument(self):
        """Event received at the beginning of XML document.
        """
        logger.debug('endDocument received')

    def startElement(self, name, attrs):
        """Event received at the beginning of each XML element.
        """
        logger.debug('startElement %s received', name)

        jnx = self.client.jnx

        if (jnx.fsm.state == jnx.fsm.ST_PREAMBLE_SENT and
                name == 'junoscript'):
                jnx.fsm.dispatch(jnx, jnx.fsm.EV_PREAMBLE_RECEIVED)
        else:
            self.tag_stack.append(name)

    def endElement(self, name):
        """Event received at the end of each XML element.
        """
        logger.debug('endElement %s received', name)

        if name == 'junoscript':
            pass
        elif self.tag_stack and self.tag_stack[-1] == name:
            self.tag_stack.pop()
        else:
            logger.warn("Malformed XML reply. Stack: %s, endElement name: %s",
                        self.tag_stack, name)
            self.client.closeConnection()
            self.client.deferred.errback()

        if not self.tag_stack:
            jnx_fsm_state = self.client.jnx.fsm.state
            logger.info("Reducing %s on %s in state %s", name,
                        self.client.jnx,
                        self.client.jnx.fsm.state_to_name(jnx_fsm_state))
            # time to reduce
            # TODO make o DOM out of received reply if not instructed otherwise
            if not self.client.skip_dom:
                self.dom = minidom.parseString(self.client.xml_text)
            else:
                self.dom = None

            if config.xml_debug and self.client.xml_text:
                config.xml_debug_fh.write('\n'.join([
                    '=== receiving xml ===',
                    self.client.xml_text,
                    '',
                    ]))

            # reactor.callLater(0, self.client.deferred.callback, self.dom)
            jnx = self.client.jnx
            jnx.fsm.dispatch(jnx, jnx.fsm.EV_RPC_REPLY, 0, self.dom)

    def characters(self, chrs):
        """Event received for chunk of text data between [start|end]Element.
        """
        logger.debug('Characters %s received', chrs)
        logger.debug('Tag stack: %s', self.tag_stack)


class JunoscriptSslClient(protocol.Protocol):

    preamble = '''
        <?xml version="1.0" encoding="us-ascii"?>
        <junoscript version="1.0" hostname="fbpush">
    '''

    rpcs = {
        'lock_configuration': {
        },
        'unlock_configuration': {
        },
        'request_login': {
            'subtags': {
                'username': [],
                'challenge_response': [],
            },
        },
        'get_configuration': {
            'attrs': {
                'changed': ['changed'],
                'database': ['candidate', 'committed'],
                'format': ['xml', 'text'],
                'compare': ['rollback'],
                'rollback': ['0'],
            },
            'subtags': {
                'configuration': [],
            },
        },
        'load_configuration': {
            'attrs': {
                'action': ['replace', 'merge'],
                'format': ['xml', 'text'],
                'rollback': range(50),
                'url': [],
            },
            'subtags': {
                'configuration_text': [],
            },
        },
        'commit_configuration': {
            'subtags': {
                'check': [False, True],
                'confirmed': [False, True],
                'confirm_timeout': [],
                'full': [False, True],
                'log': [],
            },
        },
    }

    def __init__(self):
        """
        """
        logger.info("JunoscriptSslClient: %s", self)
        self.sax_handler = SaxHandler(self)
        self.xml_reader = expatreader.ExpatParser()
        self.xml_reader.setContentHandler(self.sax_handler)
        self.skip_dom = False
        self.xml_text = ""

    def sendRpc(self, rpc, **kwargs):
        self.xml_text = ""
        signature = self.rpcs[rpc]
        tag = rpc.replace('_', '-')
        attrs = []
        subtags = []
        for name, value in kwargs.iteritems():
            if name in signature.get('attrs', []):
                if (signature['attrs'][name] and
                        value not in signature['attrs'][name]):
                    raise ValueError
                else:
                    attr = name.replace('_', '-')
                    attrs.append('%s="%s"' % (name, value))
            elif name in signature.get('subtags', []):
                if (signature['subtags'][name] and
                        value not in signature['subtags'][name]):
                    raise ValueError
                else:
                    subtag = name.replace('_', '-')
                    if value is False:
                        subtags.append("        <no-%s/>" % subtag)
                    elif value is True:
                        subtags.append("        <%s/>" % subtag)
                    else:
                        subtags.append("        <%s>%s</%s>" % (
                            subtag, value, subtag))
            else:
                raise ValueError

        if subtags:
            message = "<rpc>\n    <%s %s>\n%s\n    </%s>\n</rpc>" % (
                tag,
                " ".join(attrs),
                "\n".join(subtags),
                tag)
        else:
            message = "<rpc>\n    <%s %s/>\n</rpc>" % (
                tag,
                " ".join(attrs))

        logger.info("Sending <%s>", tag)
        if config.xml:
            for line in message.split("\n"):
                # this is hacky way to avoid logging user passwords
                # to XML transcript
                if line.find("challenge-response") != -1:
                    line = "        <challenge-response>"
                    line += "PASSWORD-REMOVED"
                    line += "</challenge-response>"
                self.jnx.xml_fh.write("-->: %s\n" % line)
        if config.xml_debug:
            # remove password
            tmp = re.sub(r'<challenge-response>.*</challenge-response>',
                         '<challenge-response>xxxxxx</challenge-response>',
                         message)
            config.xml_debug_fh.write('\n'.join([
                '=== sending xml ===',
                tmp,
                '',
                ]))
        self.transport.write(message)

    def connectionMade(self):
        logger.info("connectionMade")
        self.jnx.progress_bar[-1] = 'C'
        self.jnx.fsm.dispatch(self.jnx, self.jnx.fsm.EV_CONNECTED)

    def closeConnection(self):
        logger.info("closeConnection")
        self.transport.loseConnection()
        self.jnx.fsm.dispatch(self.jnx, self.jnx.fsm.EV_DISCONNECTED)

    def dataReceived(self, data):
        if config.xml:
            for line in data.split("\n"):
                self.jnx.xml_fh.write("<--: %s\n" % line)
        self.xml_text += data
        try:
            self.xml_reader.feed(data)
        except SAXParseException:
            logger.warn("Junk XML received from %s. Closing connection!",
                        self.jnx.device.name)
            self.closeConnection()


class JunoscriptRpc(protocol.ClientFactory):
    """
    """
    protocol = JunoscriptSslClient
    connected = 0
    diff_device_map = collections.defaultdict(list)

    def __init__(self, screen, connections, device, username, password,
                 start_time, all_diffs, diff_fh):
        self.screen = screen
        self.connections = connections
        self.configlets = []
        self.deferred = defer.Deferred()
        self.device = device
        self.config_before = None
        self.username = username
        self.password = password
        self.progress_bar = []
        self.status = ''
        self.error_msg = ''
        self.start_time = start_time
        self.commit_success = False
        self.all_diffs = all_diffs
        self.diff_fh = diff_fh

        if config.xml:
            self.xml_fh = open(os.path.join(config.LOG_DIR,
                                            device.name + ".xml"), "a", 0)
            self.xml_fh.write("-" * 80 + "\n")
        else:
            self.xml_fh = None

    def __str__(self):
        return "JNX(%s)" % self.device.name

    def addConfiglets(self, configlets):
        """Add list of configlets to be pushed to given router
        """
        self.configlets = configlets
        self.configlets_iterator = iter(configlets)
        self.configlet_names = []
        self.configlet_hash = hashlib.md5()

    def getNextConfiglet(self):
        configlet = self.configlets_iterator.next()
        self.configlet_names.append(configlet.name)
        self.configlet_hash.update(configlet.text)
        return configlet

    def updateStatus(self, progress=None,
                     error_msg=None, completed=False, spin=False):
        """Update both progress bar and last error message.

        In case of missing progress letter, flip case of last letter.
        TODO(karwas) -- if needed, throthling screen repaints can be done here.
        """
        if completed:
            self.progress_bar[-1] = self.progress_bar[-1].upper()
        if progress:
            if spin:
                self.progress_bar[-1] = progress
            else:
                self.progress_bar.append(progress)
        if error_msg:
            self.error_msg = error_msg

        reactor.callLater(0, self.screen.repaint)

    ############
    # twisted reactor fired methods

    def startedConnecting(self, connector):
        logger.info("Connecting to %s. (Already %d active workers)",
                    self.device.name, JunoscriptRpc.connected)
        JunoscriptRpc.connected += 1
        self.updateStatus('c', 'CONNECTING', False)

    def clientConnectionFailed(self, connector, reason):
        logger.warn("Connection failed to %s %s", self.device.name,
                    str(reason))
        self._handle_connection_close()

    def clientConnectionLost(self, connector, reason):
        logger.info("Connection lost to %s %s", self.device.name, str(reason))
        self._handle_connection_close()

    def _handle_connection_close(self):
        JunoscriptRpc.connected -= 1

        if self.xml_fh:
            self.xml_fh.close()

        if self.diff_fh:
            # multiple diffs mode
            if config.multiple_diffs:
                self.diff_fh.close()
            # single diff mode
            elif not JunoscriptRpc.connected:
                for diff, dnames in self.diff_device_map.iteritems():
                    self.diff_fh.write('%s diff:\n' % ', '.join(dnames))
                    self.diff_fh.write('%s\n' % diff)

        if JunoscriptRpc.connected:
            logger.info("%d workers to go!", JunoscriptRpc.connected)

    ############
    # factory inherited methods

    def buildProtocol(self, addr):
        logger.info("Build protocol event fired")
        self.proto_instance = self.protocol()
        self.proto_instance.jnx = self
        return self.proto_instance

    ############
    # Junoscript specific methods

    def sendPreamble(self):
        logger.info("sendPreamble")
        if config.xml:
            for line in self.protocol.preamble.split("\n"):
                self.xml_fh.write("-->: %s\n" % line)
        self.proto_instance.transport.write(self.protocol.preamble)
        self.fsm.state = self.fsm.ST_PREAMBLE_SENT

    def authenticate(self):
        logger.info("Authenticating on device %s with username %s",
                    self.device.name, self.username)
        self.proto_instance.sendRpc('request_login', username=self.username,
                                    challenge_response=self.password)
        self.updateStatus('a', 'AUTHENTICATING', True)

    def testAuthenticated(self, result):
        try:
            status = result.getElementsByTagName('status')
            if status:
                status = status[0].childNodes[0].data
            else:
                status = None
        except AttributeError:
            status = None
        except KeyError:
            status = None

        try:
            message = result.getElementsByTagName('message')
            if message:
                message = message[0].childNodes[0].data
            else:
                message = None
        except AttributeError:
            message = None
        except KeyError:
            message = None

        if status == 'success':
            self.updateStatus(None, 'AUTHENTICATED', True)
            self.fsm.dispatch(self, self.fsm.EV_TEST_SUCCESS)
        else:
            logger.info("Authentication on %s failed with result %s:%s",
                        self.device.name, status, message)
            logger.debug("RPC reply:\n%s", result.toprettyxml())
            self.updateStatus(
                None,
                'AUTHENTICATION: %s:%s' % (status, message),
                False)
            self.fsm.dispatch(self, self.fsm.EV_TEST_FAILURE)

    def lockConfiguration(self):
        logger.info("Locking configuration on device %s", self.device.name)
        d = self.proto_instance.sendRpc('lock_configuration')
        self.updateStatus('l', 'ACQUIRING CONFIG LOCK', True)

    def testLocked(self, result):
        try:
            xnm_error = result.getElementsByTagName('xnm:error')
        except AttributeError:
            xnm_error = None

        if not xnm_error:
            self.updateStatus(None, 'LOCK ACQUIRED', True)
            self.fsm.dispatch(self, self.fsm.EV_TEST_SUCCESS)
        else:
            try:
                message = result.getElementsByTagName('message')
                if message:
                    message = message[0].childNodes[0].data
                else:
                    message = ""
            except AttributeError:
                message = ""
            except KeyError:
                message = ""
            message = ' '.join([l.strip() for l in message.split('\n')])

            logger.info("Lock configuration on %s failed with message: %s",
                        self.device.name, message)
            logger.debug("RPC reply:\n%s", result.toprettyxml())
            self.updateStatus(
                None,
                'Locking Failed: %s' % message,
                False)
            self.fsm.dispatch(self, self.fsm.EV_TEST_FAILURE)

    def getConfiguration(self):
        logger.info("Retrieving configuration from device %s",
                    self.device.name)
        self.proto_instance.sendRpc('get_configuration', compare='rollback',
                                    rollback='0')
        self.updateStatus('r', 'READING CONFIGURATION', True)

    def uploadConfiguration(self):
        try:
            configlet = self.getNextConfiglet()
        except StopIteration:
            self.fsm.dispatch(self, self.fsm.EV_NO_MORE_CONFIGLETS)
            return

        logger.info("Uploading configlet %s on device %s",
                    configlet.name, self.device.name)
        self.proto_instance.sendRpc(
            'load_configuration',
            format='text',
            action='replace',
            configuration_text=configlet.escaped_text)
        self.updateStatus('u', 'UPLOADING CONFIGLET %s' % configlet.name, True)

    def testConfigUpload(self, result):
        try:
            xnm_error = result.getElementsByTagName('xnm:error')
        except AttributeError:
            xnm_error = None

        if not xnm_error:
            self.updateStatus(None, 'CONFIGLET UPLOADED', True)
            self.fsm.dispatch(self, self.fsm.EV_TEST_SUCCESS)
        else:
            try:
                message = result.getElementsByTagName('message')
                if message:
                    message = message[0].childNodes[0].data
                else:
                    message = ""
            except AttributeError:
                message = ""
            except KeyError:
                message = ""
            message = ' '.join([l.strip() for l in message.split('\n')])

            logger.info("Configlet upload on %s failed with message: %s",
                        self.device.name, message)
            logger.debug("RPC reply:\n%s", result.toprettyxml())
            self.updateStatus(
                None,
                'Upload Failed: %s' % message,
                False)
            self.fsm.dispatch(self, self.fsm.EV_TEST_FAILURE)

    def commitCheck(self):
        logger.info("Executing commit check on device %s", self.device.name)
        self.proto_instance.sendRpc('commit_configuration', check=True)
        self.updateStatus('h', 'EXECUTING COMMIT CHECK', True)

    def testCommitCheck(self, result):
        try:
            check_success = result.getElementsByTagName('commit-check-success')
        except AttributeError:
            check_success = None

        try:
            xnm_error = result.getElementsByTagName('xnm:error')
        except AttributeError:
            xnm_error = None

        if check_success and not xnm_error:
            self.updateStatus(None, 'COMMIT CHECKED', True)
            self.fsm.dispatch(self, self.fsm.EV_TEST_SUCCESS)
        else:
            try:
                message = result.getElementsByTagName('message')
                if message:
                    message = message[0].childNodes[0].data
                else:
                    message = ""
            except AttributeError:
                message = ""
            except KeyError:
                message = ""
            message = ' '.join([l.strip() for l in message.split('\n')])

            # see t6821880/t4062546
            if check_success:
                logger.info("Commit check succeeded on %s with message: %s",
                            self.device.name, message)
                logger.debug("RPC reply:\n%s", result.toprettyxml())
                self.updateStatus(
                    None, 'COMMIT CHECKED WITH ERROR(S): %s' % message, True)
                self.fsm.dispatch(self, self.fsm.EV_TEST_SUCCESS)
            else:
                logger.info("Commit check on %s failed with message: %s",
                            self.device.name, message)
                logger.debug("RPC reply:\n%s", result.toprettyxml())
                self.updateStatus(
                    None, 'Commit Check Failed: %s' % message, False)
                self.fsm.dispatch(self, self.fsm.EV_TEST_FAILURE)

    def diffConfiguration(self, result):
        cn = result.getElementsByTagName('configuration-output')[0]

        pattern = r'([-+]\s*/\*.*\*/\n|\s*!.*(\n|$))'
        diff = cn.childNodes[0].data
        self.diff_lines = diff if config.full_comments \
            else re.sub(pattern, '', diff)

        self.diff_device_map[self.diff_lines].append(self.device.name)
        self.all_diffs[self.device.name] = self.diff_lines
        if self.diff_fh and config.multiple_diffs:
            self.diff_fh.write("%s diff:\n%s\n" % (
                self.device.name, self.diff_lines))

        self.updateStatus(None, 'DIFF SAVED', True)
        self.fsm.dispatch(self, self.fsm.EV_NEXT)

    def commitForReal(self):
        logger.info("Executing commit on device %s", self.device.name)

        kwargs = {
            'log': "MD5(%s)=%s" % (','.join(self.configlet_names),
                                   self.configlet_hash.hexdigest()),
            'full': True
            }
        if config.no_commit_full:
            kwargs.pop('full', None)

        self.proto_instance.sendRpc('commit_configuration', **kwargs)
        self.updateStatus('.', 'EXECUTING REAL COMMIT', True)

    def testRealCommit(self, result):
        try:
            commit_success = result.getElementsByTagName('commit-success')
        except AttributeError:
            commit_success = None

        try:
            xnm_error = result.getElementsByTagName('xnm:error')
        except AttributeError:
            xnm_error = None

        if commit_success and not xnm_error:
            self.updateStatus('!', 'COMMITTED SUCCESSFULLY!', False)
            self.commit_success = True
            self.fsm.dispatch(self, self.fsm.EV_TEST_SUCCESS)
        else:
            try:
                message = result.getElementsByTagName('message')
                if message:
                    message = message[0].childNodes[0].data
                else:
                    message = ""
            except AttributeError:
                message = ""
            except KeyError:
                message = ""
            message = ' '.join([l.strip() for l in message.split('\n')])

            if commit_success:
                self.updateStatus('!', 'COMMITTED SUCCESSFULLY WITH ERROR(S):\
                                  %s' % message, False)
                self.commit_success = True
                self.fsm.dispatch(self, self.fsm.EV_TEST_SUCCESS)
            else:
                logger.info("Commit on %s failed with message: %s",
                            self.device.name, message)
                logger.debug("RPC reply:\n%s", result.toprettyxml())
                self.updateStatus(
                    None, 'Commit Failed: %s' % message, False)
                self.fsm.dispatch(self, self.fsm.EV_TEST_FAILURE)

    def commitConfirmed(self):
        logger.info("Executing commit confirmed on device %s",
                    self.device.name)
        self.proto_instance.sendRpc(
            'commit_configuration',
            confirmed=True,
            confirm_timeout=config.rollback_in,
            log="MD5(%s)=%s" % (
                ','.join(self.configlet_names),
                self.configlet_hash.hexdigest()))
        self.updateStatus('.', 'EXECUTING COMMIT CONFIRMED', True)

    def testCommitConfirmed(self, result):
        try:
            commit_success = result.getElementsByTagName('commit-success')
        except AttributeError:
            commit_success = None

        try:
            xnm_error = result.getElementsByTagName('xnm:error')
        except AttributeError:
            xnm_error = None

        if commit_success and not xnm_error:
            self.updateStatus('!', 'COMMITTED CONFIRMED SUCCESSFULLY!', False)
            self.rollback_timer = config.rollback_in * 60
            self.commit_timer = config.confirmed_in * 60
            self.progress_chr = itertools.cycle('\|/-')
            self.fsm.dispatch(self, self.fsm.EV_TEST_SUCCESS)
        else:
            try:
                message = result.getElementsByTagName('message')
                if message:
                    message = message[0].childNodes[0].data
                else:
                    message = ""
            except AttributeError:
                message = ""
            except KeyError:
                message = ""
            message = ' '.join([l.strip() for l in message.split('\n')])

            if commit_success:
                self.updateStatus('!', 'COMMITTED CONFIRMED WITH ERROR(S): %s'
                                  % message, False)
                self.rollback_timer = config.rollback_in * 60
                self.commit_timer = config.confirmed_in * 60
                self.progress_chr = itertools.cycle('\|/-')
                self.fsm.dispatch(self, self.fsm.EV_TEST_SUCCESS)
            else:
                logger.info("Commit on %s failed with message: %s",
                            self.device.name, message)
                logger.debug("RPC reply:\n%s", result.toprettyxml())
                self.updateStatus(
                    None, 'Commit Confirmed Failed: %s' % message, False)
                self.fsm.dispatch(self, self.fsm.EV_TEST_FAILURE)

    def waitForConfirm(self):
        assert self.fsm.state == self.fsm.ST_WAIT_FOR_CONFIRM,\
            "Unexpected state in waitForConfirm: %s" % (
                self.fsm.state_to_name(self.fsm.state)
            )
        self.updateStatus(
            self.progress_chr.next(),
            error_msg='AUTOMATIC COMMIT IN %d SECONDS' % self.commit_timer,
            completed=False,
            spin=True
        )
        assert self.commit_timer >= 1,\
            "Unexpected value of commit_timer!"
        self.commit_timer -= 1
        if self.commit_timer:
            self.fsm.dispatch(self, self.fsm.EV_WAIT, 1)
        else:
            self.fsm.dispatch(self, self.fsm.EV_CONFIRM)

    def waitForCommit(self):
        pass

    def waitForRetry(self):
        pass

    def waitForAll(self):
        ST_WAIT_FOR_ALL = self.fsm.ST_WAIT_FOR_ALL
        if all([jnx.fsm.state == ST_WAIT_FOR_ALL for jnx in self.connections]):
            for jnx in self.connections:
                jnx.fsm.dispatch(jnx, jnx.fsm.EV_ALL_READY)

    def closeConnection(self):
        logger.info("Closing connection to device %s", self.device.name)
        self.proto_instance.closeConnection()

    def decrementJobs(self):
        logger.info("Removing pending jobs for device %s", self.device.name)

    def unlock_configuration(self, result):
        logger.info("Unlocking configuration on device %s", self.device.name)
        d = self.proto_instance.sendRpc('unlock_configuration')
        return d

    def rollback_configuration(self, result, configlet):
        logger.info("Rolling back configuration on device %s",
                    self.device.name)
        d = self.proto_instance.sendRpc('load_configuration',
                                        rollback=0)
        return d

    def rollBack(self):
        self.proto_instance.sendRpc('load_configuration', rollback=1)
        self.updateStatus('r', 'ROLLBACKING', True)

    def testRollBack(self, result):
        try:
            xnm_error = result.getElementsByTagName('xnm:error')
        except AttributeError:
            xnm_error = None

        if not xnm_error:
            self.updateStatus(None, 'ROLLBACK LAST CONFIG', True)
            self.fsm.dispatch(self, self.fsm.EV_ROLLBACK_SUCCESS)
        else:
            self.updateStatus(None, 'ROLLBACK FAIL', True)
            self.fsm.dispatch(self, self.fsm.EV_ROLLBACK_FAIL)

    def rollBackCommit(self):
        self.proto_instance.sendRpc(
            'commit_configuration', log="rollback 1")
        self.updateStatus('c', 'COMMITTING ROLLBACK', True)

    def testRollBackCommit(self, result):
        try:
            commit_success = result.getElementsByTagName('commit-success')
        except AttributeError:
            commit_success = None

        try:
            xnm_error = result.getElementsByTagName('xnm:error')
        except AttributeError:
            xnm_error = None

        message = ''

        if xnm_error:
            try:
                message = result.getElementsByTagName('message')
                message = message[0].childNodes[0].data
                message = ' '.join([l.strip() for l in message.split('\n')])
            except (AttributeError, KeyError):
                pass

        if commit_success:
            if xnm_error:
                self.updateStatus(None, 'ROLLBACK COMMIT SUCCESSFULLY WITH \
                                  ERRORS: %s' % message, True)
            else:
                self.updateStatus(None, 'ROLLBACK COMMIT SUCCESSFULLY', True)
            self.fsm.dispatch(self, self.fsm.EV_ROLLBACK_COMMIT_SUCCESS)
        else:
            self.updateStatus(None, 'ROLLBACK COMMIT FAIL: %s' % message, True)
            self.fsm.dispatch(self, self.fsm.EV_ROLLBACK_COMMIT_FAIL)
