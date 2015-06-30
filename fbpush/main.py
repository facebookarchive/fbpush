# Copyright (c) 2015-present, Facebook, Inc.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree. An additional grant
# of patent rights can be found in the PATENTS file in the same directory.


"""Push script for Juniper devices.
"""

import curses
import getpass
import hashlib
import json
import logging
import logging.handlers as log_handler
import os
import re
import requests
import sys
import time

from twisted.internet import reactor, ssl

from . import push
from . import config, params


logger = logging.getLogger()


def init_logging():
    # TODO log level is not currently cofigurable
    log = logging.getLogger()
    log_level = config.LOG_LEVELS.get(config.LOG_VERBOSITY,
                                      config.LOG_DEBUG_VERBOSITY)
    log.setLevel(log_level)
    dirname = os.path.dirname(config.LOG_FILENAME)
    if dirname and not os.path.exists(dirname):
        try:
            os.makedirs(dirname)
        except OSError as ex:
            sys.stderr.write('log dir creation failed: %s\n' % str(ex))
            sys.exit(1)
    LBC = config.LOG_BACKUP_COUNT
    handler = log_handler.RotatingFileHandler(config.LOG_FILENAME,
                                              maxBytes=config.LOG_MAX_BYTES,
                                              backupCount=LBC)
    handler.setFormatter(logging.Formatter(config.LOG_FORMAT))
    handler.setLevel(log_level)
    log.addHandler(handler)


def get_device():
    """Returns list of devices after first stage filtering

    This returns list of devices after the first stage filtering.
    It creates the first stage filter based on the command line
    arguments and returns a list of devices after the first stage filtering.

    returns: list of network_devices.
    """
    nd_filter = {}
    for attr in config.OPTION_ATTRS[:-1]:
        nd_filter[attr] = getattr(config, attr)
        nd_filter[attr[:-1] + '_regex'] = getattr(config, attr[:-1] + '_regex')
        nd_filter['exclude_' + attr[:-1] + '_regex'] = getattr(config,
                                                               'exclude_' +
                                                               attr[:-1] +
                                                               '_regex')

    return network_filter(nd_filter)


class Device():

    def __init__(self, details):
        if 'ip' in details:
            self.ip = details['ip']

            for attr in config.OPTION_ATTRS[:-1]:
                if attr[:-1] in details:
                    setattr(self, attr[:-1], details[attr[:-1]])
                else:
                    setattr(self, attr[:-1], '')
        else:
            device_detail = ''
            for attr in config.OPTION_ATTRS[:-1]:
                if attr[:-1] in details:
                    device_detail += attr[:-1] + ' : ' + details[attr[:-1]]
                    device_detail += ', '
            print("'%s' does not have an ip, skipping" % device_detail)

    def __getattr__(self, param):
        """Attribute accessor helper."""
        try:
            return getattr(self, param)
        except:
            raise AttributeError('No attribute named %s' % param)


def network_filter(nd_filter):
    """Does the first stage filtering

    This loads the config file containing network_devices information
    and does the filtering based on the dictionary values.

    params exact_filters: dictionary object which contains the keys for which
        exacting matching is done.
    params regex_filters: dictionary objects which contains the keys for
        which regex matching is to be done.

    returns: list of filtered devices.
    """
    # does the first stage filtering
    config_file = config.CONFIG_DIR + config.DEFAULT_DEVICE_FILE
    device_details = json.loads(open(config_file, 'r').read())
    for key, values in nd_filter.iteritems():
        if values:
            if key.startswith('exclude_'):
                ckey = key.split('_')[1]
                device_details['config'] = filter(lambda x:
                                                  re.match(values, x[ckey])
                                                  is None,
                                                  device_details['config'])

            elif key.endswith('_regex'):
                ckey = key.split('_')[0]
                device_details['config'] = filter(lambda x:
                                                  re.match(values, x[ckey])
                                                  is not None,
                                                  device_details['config'])

            else:
                device_list = []
                for value in values:
                    temp_list = filter(lambda x: x[key[:-1]] == value,
                                       device_details['config'])
                    device_list.extend(temp_list)
                device_details['config'] = device_list
    return [Device(device_detail)
            for device_detail in device_details['config']]


def configure():
    """Parse command line options and integrate with settings.
    """

    init_logging()

    if config.xml_debug:
        PrintReminder.yellow(
                'XML debug file at %s' % config.XML_DEBUG_FILENAME)
        config.xml_debug_fh = open(config.XML_DEBUG_FILENAME, 'w', 0)

    if not config.force:
        for fname in params:
            if not (fname.endswith('.conf') or fname.startswith('http')):
                print('File not found. File name should end with .conf'
                      ' or start with http')
                sys.exit(1)

    if config.dry_run:
        # In dry_run mode, commit_confirmed is not needed
        config.confirmed_in = None

    if config.confirmed_in:
        if config.dry_run:
            print(''.join([
                'Commit confirm and check (aka dry_run) ',
                'are mutually exclusive!',
                ]))
            sys.exit(1)
        if config.rollback_in:
            if config.rollback_in <= config.confirmed_in:
                print('ROLLBACK_IN must be > automatic CONFIRMED_IN!')
                sys.exit(1)
        else:
            config.rollback_in = config.confirmed_in + 1

    return params


class Configlet(object):
    """
    """
    _JUNIPER_CONFIGLETS_TAGS = re.compile(r'''
        \s*\/\*\s*
        \$Id:?[^\$]*\$\s*
        (?P<tags>.*?)
        \s*\*/
        ''', re.VERBOSE)

    _TAG_GETTERS = {
        'name': lambda d: d.name,
        'location': lambda d: d.location,
        'vendor': lambda d: d.vendor,
        'ip': lambda d: d.ip,
    }

    def __init__(self, fname, lines):

        self.fname = fname
        self.tags = {}

        self.lines = lines

        if not ''.join(self.lines).strip():
            self.is_empty = True
        else:
            self.is_empty = False

        for line in self.lines:
            re_obj = self.is_tag_line(line)

            if re_obj:
                for tag in re_obj.groupdict()['tags'].split(';'):
                    tag_name, tag_values = tag.split(':')
                    tag_name = tag_name.strip()
                    if tag_name.endswith('s'):
                        tag_name = tag_name[:-1]
                        tag_values = [
                            v.strip() for v in tag_values.split(',')]
                    else:
                        tag_values = [tag_values.strip()]
                    self.tags[tag_name] = tag_values
                break

        m = hashlib.md5()
        m.update(self.text_no_comment)
        self.gen_md5 = m.hexdigest()

        # check file md5 if exists
        # md5 should be generated EXCLUDE the tag line
        tag_md5 = self.tags.get('md5')
        if tag_md5:
            if self.gen_md5.lower() != tag_md5[0].lower():
                PrintReminder.yellow(
                        '%s seems modified as md5 not matched.' % self.name)

    def __hash__(self):
        return hash(self.fname) + hash(self.text)

    def valid_for(self, device):
        """
        """
        if not self.tags and not config.force:
            return False

        for tag_name, values in self.tags.iteritems():
            if tag_name.startswith('exclude_'):

                tag_getter = self._TAG_GETTERS[tag_name[len('exclude_'):]]
                if tag_getter(device) in values:
                    return False
            elif tag_name.endswith('_regex'):

                tag_getter = self._TAG_GETTERS[tag_name[:-len('_regex')]]
                if not re.match(values[0], tag_getter(device)):
                    return False
            elif tag_name == 'md5':
                pass
            else:
                tag_getter = self._TAG_GETTERS[tag_name]

                if tag_getter(device) not in values:
                    return False

        return True

    def is_comment_line(self, line):
        if (line.strip().startswith('/*') or
                line.strip().startswith('!')):
            return True
        return False

    def is_tag_line(self, line):
        re_obj = self._JUNIPER_CONFIGLETS_TAGS.match(line)
        return re_obj

    @property
    def name(self):
        return self.fname

    @property
    def text(self):
        """"Return text of configlet exactly like in the original file.
        """
        return ''.join(self.lines)

    @property
    def text_no_tag(self):
        """"Return text of configlet exactly like in the original file.
            WITHOUT the tag line
        """
        return ''.join([line for line in self.lines
                       if not self.is_tag_line(line)])

    @property
    def text_no_comment(self):
        """"Return text of configlet exactly like in the original file.
            WITHOUT comment lines
        """
        return ''.join([line for line in self.lines
                       if not self.is_comment_line(line)])

    @property
    def escaped_text(self):
        """"Return escaped text of configlet ready for load_configuration RPC.
        """
        return ''.join(
            l.replace('<', '&lt;').replace('>', '&gt;')
            for l in self.lines)


class FSM(object):
    """Finite State Machine base class
    """
    def __init__(self, init_state, states=None, events=None):
        """
        """
        if states:
            for k, v in states.iteritems():
                setattr(self, k, v)
        if events:
            for k, v in events.iteritems():
                setattr(self, k, v)

        self.transitions = self.TRANSITIONS.copy()
        self.state = init_state

    def add_transition(self, from_state, event, new_state, function):
        self.transitions[from_state, event] = (new_state, function)

    def state_to_name(self, state):
        for k, v in self.__class__.__dict__.iteritems():
            if k.startswith('ST') and v == state:
                return k
        return 'UNKNOWN_STATE'

    def event_to_name(self, event):
        for k, v in self.__class__.__dict__.iteritems():
            if k.startswith('EV') and v == event:
                return k
        return 'UNKNOWN_EVENT'

    def dispatch(self, obj, event, delay=0, *args):
        if delay:
            logger.info(
                        'Delayed dispatching. '
                        'Delay: %d, Object: %s, State: %s, Event: %s',
                        delay, obj, self.state_to_name(self.state),
                        self.event_to_name(event)
                       )
            reactor.callLater(delay, self.dispatch, obj, event, delay=0, *args)
            return

        logger.info('Dispatching. Object: %s, State: %s, Event: %s',
                    obj, self.state_to_name(self.state),
                    self.event_to_name(event))
        try:
            (state, function) = self.transitions[self.state, event]
        except KeyError:
            (state, function) = self.transitions[None, event]

        logger.info('New state: %s, Function: %s, delay=%d',
                    self.state_to_name(state), function, delay)
        if state:
            self.state = state

        if callable(function):
            reactor.callLater(0, function, *args)
        elif function:
            # this will return bound method of the obj
            function = getattr(obj, function)
            reactor.callLater(0, function, *args)
        else:
            logger.info('Injecting new event EV_NEXT for object: %s', obj)
            reactor.callLater(0, self.dispatch, obj, self.EV_NEXT)


class FSM_PushCurses(FSM):
    """
    """
    #####################################
    # States
    ST_ANY = None
    ST_THE_SAME = None

    ST_START = 0
    ST_STATUS_SCREEN = 1
    ST_HELP_SCREEN = 2
    ST_END = 99

    #####################################
    # Events
    EV_NEXT = 0

    EV_KEY_HELP = 101
    EV_KEY_RETRY = 102
    EV_KEY_QUIT = 109

    #####################################
    # Transitions
    TRANSITIONS = {
        (ST_ANY, EV_KEY_RETRY):
            (ST_THE_SAME, 'scheduleAgain'),
    }


class FSM_PushJnx(FSM):
    """
    """
    #####################################
    # States
    ST_ANY = None
    ST_THE_SAME = None

    ST_START = 0
    ST_CONNECTED = 1
    ST_CONNECTION_CLOSING = 8
    ST_CONNECTION_CLOSED = 9

    ST_PREAMBLE_SENT = 10
    ST_PREAMBLE_RECEIVED = 11

    ST_AUTHENTICATING = 20
    ST_AUTH_TESTING = 21
    ST_AUTHENTICATED = 22

    ST_CONFIG_LOCKING = 30
    ST_CONFIG_LOCK_TESTING = 31
    ST_CONFIG_LOCKED = 32
    ST_CONFIG_LOCK_FAILED = 33
    ST_WAIT_FOR_ALL = 38
    ST_WAIT_FOR_RETRY = 39

    ST_CONFIG_UPLOADING = 40
    ST_CONFIG_UPLOAD_TESTING = 41
    ST_CONFIG_UPLOAD_NEXT = 42
    ST_CONFIG_UPLOAD_FINISHED = 42
    ST_CONFIG_UPLOAD_FAILED = 43

    ST_COMMIT_CHECKING = 50
    ST_COMMIT_CHECK_TESTING = 51
    ST_COMMIT_CHECK_FAILED = 52
    ST_COMMIT_CHECK_OK = 53

    ST_CONFIG_READING2 = 70
    ST_CONFIG_READ2 = 71
    ST_CONFIG_DIFFING = 72

    ST_COMMITTING_FOR_REAL = 100
    ST_COMMIT_FOR_REAL_TESTING = 101
    ST_COMMIT_FOR_REAL_FAILED = 102

    ST_COMMITTING_CONFIRMED = 110
    ST_COMMIT_CONFIRMED_TESTING = 111
    ST_WAIT_FOR_CONFIRM = 112

    ST_ROLLBACK = 300
    ST_ROLLBACK_TESTING = 301
    ST_ROLLBACK_COMMIT = 302
    ST_ROLLBACK_COMMIT_TESTING = 303

    ST_FINISHED = 999

    #####################################
    # Events
    EV_NEXT = 0

    EV_CONNECTED = 1
    EV_DISCONNECTED = 2

    EV_PREAMBLE_RECEIVED = 5

    EV_RPC_REPLY = 10
    EV_RPC_ERROR = 19

    EV_TEST_SUCCESS = 20
    EV_TEST_FAILURE = 29

    EV_RETRY = 30

    EV_NO_MORE_CONFIGLETS = 40

    EV_COUNTDOWN = 50
    EV_WAIT = 51
    EV_CONFIRM = 52
    EV_ROLLBACK = 59
    EV_ROLLBACK_SUCCESS = 60
    EV_ROLLBACK_FAIL = 61
    EV_ROLLBACK_COMMIT_SUCCESS = 62
    EV_ROLLBACK_COMMIT_FAIL = 63

    EV_ALL_READY = 90

    #####################################
    # Transitions
    TRANSITIONS = {
        (ST_START, EV_CONNECTED):
            (ST_CONNECTED, 'sendPreamble'),

        (ST_ANY, EV_DISCONNECTED):
            (ST_CONNECTION_CLOSED, None),

        (ST_PREAMBLE_SENT, EV_PREAMBLE_RECEIVED):
            (ST_AUTHENTICATING, 'authenticate'),

        (ST_AUTHENTICATING, EV_RPC_REPLY):
            (ST_AUTH_TESTING, 'testAuthenticated'),
        (ST_AUTH_TESTING, EV_TEST_SUCCESS):
            (ST_CONFIG_LOCKING, 'lockConfiguration'),
        (ST_AUTH_TESTING, EV_TEST_FAILURE):
            (ST_CONNECTION_CLOSING, 'closeConnection'),

        (ST_CONFIG_LOCKING, EV_RPC_REPLY):
            (ST_CONFIG_LOCK_TESTING, 'testLocked'),
        (ST_CONFIG_LOCK_TESTING, EV_TEST_SUCCESS):
            (ST_CONFIG_UPLOADING, 'uploadConfiguration'),
        (ST_CONFIG_LOCK_TESTING, EV_TEST_FAILURE):
            (ST_CONFIG_LOCK_FAILED, None),

        (ST_CONFIG_LOCK_FAILED, EV_NEXT):
            (ST_WAIT_FOR_RETRY, 'waitForRetry'),
        (ST_WAIT_FOR_RETRY, EV_RETRY):
            (ST_CONFIG_LOCKING, 'lockConfiguration'),

        (ST_CONFIG_UPLOADING, EV_NO_MORE_CONFIGLETS):
            (ST_COMMIT_CHECKING, 'commitCheck'),
        (ST_CONFIG_UPLOADING, EV_RPC_REPLY):
            (ST_CONFIG_UPLOAD_TESTING, 'testConfigUpload'),

        (ST_CONFIG_UPLOAD_TESTING, EV_TEST_SUCCESS):
            (ST_CONFIG_UPLOADING, 'uploadConfiguration'),
        (ST_CONFIG_UPLOAD_TESTING, EV_TEST_FAILURE):
            (ST_CONFIG_UPLOAD_FAILED, 'closeConnection'),


        (ST_COMMIT_CHECKING, EV_RPC_REPLY):
            (ST_COMMIT_CHECK_TESTING, 'testCommitCheck'),
        (ST_COMMIT_CHECK_TESTING, EV_TEST_FAILURE):
            (ST_COMMIT_CHECK_FAILED, 'closeConnection'),
        (ST_COMMIT_CHECK_TESTING, EV_TEST_SUCCESS):
            (ST_COMMIT_CHECK_OK, None),

        # following transition will be overriten if "no-diff mode" is selected
        (ST_COMMIT_CHECK_OK, EV_NEXT):
            (ST_CONFIG_READING2, 'getConfiguration'),
        (ST_CONFIG_READING2, EV_RPC_REPLY):
            (ST_CONFIG_DIFFING, 'diffConfiguration'),


        # following transition will be overriten if "no-dry-run" is selected
        (ST_CONFIG_DIFFING, EV_NEXT):
            (ST_FINISHED, 'closeConnection'),


        (ST_COMMITTING_FOR_REAL, EV_RPC_REPLY):
            (ST_COMMIT_FOR_REAL_TESTING, 'testRealCommit'),
        (ST_COMMIT_FOR_REAL_TESTING, EV_TEST_SUCCESS):
            (ST_FINISHED, 'closeConnection'),
        (ST_COMMIT_FOR_REAL_TESTING, EV_TEST_FAILURE):
            (ST_FINISHED, 'closeConnection'),

        (ST_COMMITTING_CONFIRMED, EV_RPC_REPLY):
            (ST_COMMIT_CONFIRMED_TESTING, 'testCommitConfirmed'),
        (ST_COMMIT_CONFIRMED_TESTING, EV_TEST_SUCCESS):
            (ST_WAIT_FOR_CONFIRM, 'waitForConfirm'),
        (ST_WAIT_FOR_CONFIRM, EV_WAIT):
            (ST_THE_SAME, 'waitForConfirm'),
        (ST_ANY, EV_WAIT):
            (ST_THE_SAME, 'waitForCommit'),
        (ST_WAIT_FOR_CONFIRM, EV_CONFIRM):
            (ST_COMMITTING_FOR_REAL, 'commitForReal'),

        # for ppl exit when some devices are in commit confirm state
        (ST_WAIT_FOR_CONFIRM, EV_ROLLBACK):
            (ST_ROLLBACK, 'rollBack'),
        (ST_ROLLBACK, EV_RPC_REPLY):
            (ST_ROLLBACK_TESTING, 'testRollBack'),
        (ST_ROLLBACK_TESTING, EV_ROLLBACK_SUCCESS):
            (ST_ROLLBACK_COMMIT, 'rollBackCommit'),
        (ST_ROLLBACK_TESTING, EV_ROLLBACK_FAIL):
            (ST_FINISHED, 'closeConnection'),

        (ST_ROLLBACK_COMMIT, EV_RPC_REPLY):
            (ST_ROLLBACK_COMMIT_TESTING, 'testRollBackCommit'),
        (ST_ROLLBACK_COMMIT_TESTING, EV_ROLLBACK_COMMIT_SUCCESS):
            (ST_FINISHED, 'closeConnection'),
        (ST_ROLLBACK_COMMIT_TESTING, EV_ROLLBACK_COMMIT_FAIL):
            (ST_FINISHED, 'closeConnection'),

        (ST_COMMIT_CONFIRMED_TESTING, EV_TEST_FAILURE):
            (ST_FINISHED, 'closeConnection'),

        (ST_CONNECTION_CLOSED, EV_NEXT):
            (ST_THE_SAME, 'decrementJobs'),
    }

    def addDiffing(self, jnx):
        self.add_transition(
            self.ST_COMMIT_CHECK_OK, self.EV_NEXT,
            self.ST_CONFIG_READING2, jnx.getConfiguration)
        if config.dry_run:
            self.add_transition(
                self.ST_CONFIG_DIFFING, self.EV_NEXT,
                self.ST_FINISHED, jnx.closeConnection)
        elif config.atomic:
            self.add_transition(
                self.ST_CONFIG_DIFFING, self.EV_NEXT,
                self.ST_WAIT_FOR_ALL, jnx.waitForAll)
            if config.confirmed_in:
                self.add_transition(
                    self.ST_WAIT_FOR_ALL, self.EV_ALL_READY,
                    self.ST_COMMITTING_CONFIRMED, jnx.commitConfirmed)
            else:
                self.add_transition(
                    self.ST_WAIT_FOR_ALL, self.EV_ALL_READY,
                    self.ST_COMMITTING_FOR_REAL, jnx.commitForReal)
        else:  # commit for real, regardless of others
            if config.confirmed_in:
                self.add_transition(
                    self.ST_CONFIG_DIFFING, self.EV_NEXT,
                    self.ST_COMMITTING_CONFIRMED, jnx.commitConfirmed)
            else:
                self.add_transition(
                    self.ST_CONFIG_DIFFING, self.EV_NEXT,
                    self.ST_COMMITTING_FOR_REAL, jnx.commitForReal)

    def addCommits(self, jnx):
        self.add_transition(
            self.ST_CONFIG_LOCKED, self.EV_NEXT,
            self.ST_CONFIG_UPLOADING, jnx.uploadConfiguration)
        if config.dry_run:
            self.add_transition(
                self.ST_COMMIT_CHECK_OK, self.EV_NEXT,
                self.ST_FINISHED, jnx.closeConnection)
        elif config.atomic:
            self.add_transition(
                self.ST_COMMIT_CHECK_OK, self.EV_NEXT,
                self.ST_WAIT_FOR_ALL, jnx.waitForAll)
            if config.confirmed_in:
                self.add_transition(
                    self.ST_WAIT_FOR_ALL, self.EV_ALL_READY,
                    self.ST_COMMITTING_CONFIRMED, jnx.commitConfirmed)
            else:
                self.add_transition(
                    self.ST_WAIT_FOR_ALL, self.EV_ALL_READY,
                    self.ST_COMMITTING_FOR_REAL, jnx.commitForReal)
        else:  # commit for real, regardless of others
            if config.confirmed_in:
                self.add_transition(
                    self.ST_COMMIT_CHECK_OK, self.EV_NEXT,
                    self.ST_COMMITTING_CONFIRMED, jnx.commitConfirmed)
            else:
                self.add_transition(
                    self.ST_COMMIT_CHECK_OK, self.EV_NEXT,
                    self.ST_COMMITTING_FOR_REAL, jnx.commitForReal)


class PrintReminder(object):

    @staticmethod
    def yellow(msg):
        print('\033[1;33m%s\033[0m ' % msg)

    @staticmethod
    def red(msg):
        print('\033[1;31m%s\033[0m ' % msg)

    @staticmethod
    def purple(msg):
        print('\033[1;35m%s\033[0m ' % msg)

    @staticmethod
    def purple_bg(msg):
        print('\033[1;45m%s\033[0m ' % msg)

    @staticmethod
    def white(msg):
        print('%s' % msg)

    def print_reminder(self, dry_run, ndevice, diff_dir):
        contents = open(config.REMINDER_PATH, 'r').read()
        reminders = json.loads(contents)
        reminder_map = reminders['reminder_msg']

        print('')
        self.yellow('%s' % '=' * 80)
        self.yellow('%30s%s%30s' % (' ', 'Friendly Reminder', ' '))

        for color, rm_list in reminder_map.iteritems():
            f = getattr(self, color, None)
            if f:
                for msg in rm_list:
                    f('- %s ' % msg)

        if not dry_run:
            self.red('\n- This is NOT DRY_RUN! (%d devices, diff_dir=%s)' % (
                ndevice, diff_dir))
        else:
            print('\n- This is DRY_RUN. (%d devices, diff_dir=%s)' % (
                ndevice, diff_dir))
        self.yellow('%s' % '=' * 80)


def is_dry_run_prev(configlets):
    '''
    check DRY_RUN_HASH_FILENAME hash content == md5(all config content)
    if not equal return false
    '''
    try:
        fh = open(config.DRY_RUN_HASH_FILENAME, 'r')
        read_md5 = fh.readline()
        tmp = sorted(configlets, key=lambda c: c.name)
        gen_md5 = ''
        for configlet in tmp:
            gen_md5 += configlet.gen_md5
        return read_md5 == gen_md5
    except Exception as ex:
        # most likely haven't dry_run yet, file doesn't exist
        print('Error reading %s ex: %s' % (
            config.DRY_RUN_HASH_FILENAME, str(ex)))
        return False

    return False


def update_dry_run_hash(configlets):
    '''
    To indicate user run dry_run on these files
    '''
    try:
        fh = open(config.DRY_RUN_HASH_FILENAME, 'w')
        tmp = sorted(configlets, key=lambda c: c.name)
        for configlet in tmp:
            fh.write(configlet.gen_md5)
    except Exception as ex:
        print('Error writing %s ex: %s' % (
            config.DRY_RUN_HASH_FILENAME, str(ex)))


def get_file_from_http(url):
    print('downloading from %s ' % url)
    try:
        ret = requests.get(url, timeout=300)
        if ret.status_code != requests.codes.ok:
            print('error on reading %s ' % url)
            sys.exit(-1)
    except Exception as ex:
        print('Cannot download %s, error %s' % (url, str(ex)))
        sys.exit(-1)
    return ret.content.splitlines(True)


def get_configlets(file_or_url):
    ret = set()
    if file_or_url.startswith('http'):
        lines = get_file_from_http(file_or_url)
        fname = file_or_url.split('/')[-1]
        ret.add(Configlet(fname, lines))
    elif config.force or file_or_url.endswith('.conf'):
        lines = open(file_or_url).readlines()
        fname = os.path.basename(file_or_url)
        ret.add(Configlet(fname, lines))

    return ret


def curses_push(args):
    """
    """
    devices = get_device()
    if len(devices) == 0:
        print('No device is selected.')
        return

    logger.debug('%d devices selected' % len(devices))
    configlets = set()
    has_error = False

    if config.stdin:
        lines = sys.stdin.readlines()
        configlets.add(Configlet(config.STDIN_FNAME, lines))
    else:
        for arg in args:
            configlets.update(get_configlets(arg))

    # some error checking for configlets
    for configlet in configlets:
        # warn empty file
        if configlet.is_empty:
            print('WARNING: file %s is empty' % configlet.name)
            continue
        # Configlet error checking:(1) check vendor tag and (2) unicode
        # check vendor tag
        if configlet.tags.get('vendor') is None:
            print('Configlet %s needs to have vendor tag' % configlet.name)
            has_error = True
        # check for unicode
        for line in configlet.lines:
            try:
                line.decode('ascii')
            except UnicodeDecodeError:
                print('configlets %s contains unicode at line\n-%s' %
                      (configlet.name, line))
                has_error = True

    if has_error:
        return

    if config.gen_md5:
        for configlet in configlets:
            print('%s %s' % (configlet.name, configlet.gen_md5))
        return

    work_to_do = False
    dry_run_needed = False

    for device in devices:

        device.configlets = []

        for configlet in configlets:
            if configlet.valid_for(device) and not configlet.is_empty:
                device.configlets.append(configlet)
                work_to_do = True
                if device.vendor in config.DRY_RUN_VENDORS:
                    dry_run_needed = True

    ndevice = 0
    # dict{device_name:diff}
    all_diffs = {}
    if work_to_do:
        for device in devices:
            if not device.configlets:
                continue

            ndevice += 1
            if device.vendor != 'juniper':
                if config.atomic:
                    print('atomic is only for juniper, but %s is %s'
                          % (device.name, device.vendor))
                    return

            if device.vendor not in config.DRY_RUN_VENDORS:
                if config.dry_run:
                    print('dry_run is not supported for %s (%s)'
                          % (device.name, device.vendor))
                    return

    try:
        PrintReminder().print_reminder(
                config.dry_run, ndevice, config.diff_dir)
    except Exception as ex:
        print('Cannot read reminder from configerator %s' % str(ex))

    if work_to_do:
        # create diff dir if not exist
        if config.diff_dir:
            try:
                os.makedirs(config.diff_dir)
            except:
                pass

        # force users must dry_run before real push
        if (dry_run_needed and
                not config.dry_run and
                not is_dry_run_prev(configlets) and
                not config.clowntown):
            PrintReminder.yellow(''.join([
                '\nYour previous "fbpush" is not dry_run for \n%s\n'
                % args, 'Or confilget(s) are modified? ',
                'Please dry_run and review the diff again.',
                ]))
            return

        if config.clowntown:
            PrintReminder.red(
                    'You by pass dry_run check, '
                    'I believe you have a legit reason')

        if config.stdin:
            # release previous piped program fd
            # otherwise curse lib will fail
            fd = open('/dev/tty')
            os.dup2(fd.fileno(), 0)

        if not config.username:
            config.username = getpass.getuser()
        while not config.password:
            config.password = getpass.getpass()
            if not config.password:
                print('Password cannot be empty!')

        connections = []
        try:
            stdscr = curses.initscr()
            screen = push.Screen(connections, stdscr)
            screen.fsm = FSM_PushCurses(init_state=FSM_PushCurses.ST_START)
            reactor.addReader(screen)

            start_time = int(time.time())

            # one single diff file
            if config.diff_dir and not config.multiple_diffs:
                diff_fh = open(os.path.join(
                               config.diff_dir,
                               config.FBPUSH_SINGLE_FILENAME), 'w', 0)
            for device in devices:
                if not device.configlets:
                    continue

                # multiple diff files
                if config.diff_dir and config.multiple_diffs:
                    diff_fh = open(os.path.join(
                        config.diff_dir, device.name + '.diff'), 'w', 0)

                if device.vendor == 'juniper':
                    jnx = push.JunoscriptRpc(
                        screen,
                        connections,
                        device,
                        config.username,
                        config.password,
                        start_time,
                        all_diffs,
                        diff_fh)
                    jnx.addConfiglets(device.configlets)
                    jnx.fsm = FSM_PushJnx(init_state=FSM_PushJnx.ST_START)

                    if config.diff_dir:
                        jnx.fsm.addDiffing(jnx)
                    else:  # bypass diffing
                        jnx.fsm.addCommits(jnx)

                    reactor.connectSSL(
                        # juniper is not connect thru fcr
                        # need to specify ip here
                        device.ip,
                        config.XNM_SSL_PORT,
                        jnx,
                        ssl.ClientContextFactory())
                    connections.append(jnx)

            logger.info('Launching reactor with %d connetions',
                        len(connections))
            reactor.run()
        finally:
            curses.nocbreak()
            stdscr.keypad(0)
            stdscr.standout()
            curses.echo()
            curses.endwin()

            # write md5 of all files (args) into a file,
            # to indicate user has dry_run
            if config.dry_run:
                update_dry_run_hash(configlets)

            for connection in connections:
                if not connection.diff_fh.closed:
                    connection.diff_fh.close()

            return all_diffs
    else:
        print('Nothing to do. No match between configlets and devices')
        config.parser.print_help()


def print_diff_preview(all_diffs):
    if not all_diffs:
        return

    diff_list = [(device_name, diff)
                 for device_name, diff in all_diffs.iteritems()]
    diff_list = sorted(diff_list, key=lambda x: len(x[1]))

    # print diff with max no. of lines
    device_name, diff = diff_list[-1]
    PrintReminder.purple('Showing one of the biggest diff at %s' % device_name)
    print(diff)

    # print a summary for each devices, how many lines are in each diff
    PrintReminder.purple('Diffs summary:')
    for device_name, diff in diff_list:
        PrintReminder.purple(
                '%s diff contains %d lines' % (
                    device_name, len(diff.splitlines())))

    PrintReminder.purple_bg(
            'Diffs are saved at %s. Please review with care!' %
            config.diff_dir)


def main(args):
    """Loads and executes the main logic; must happen after configuration
    """
    logger.info('Starting push with arguments %s', sys.argv[1:])
    all_diffs = curses_push(args)
    print_diff_preview(all_diffs)


def run_main_and_report(args):
    """Wrapper around main()"""
    try:
        main(args)
    except Exception as exc:
        logger.exception('Push failed.')
        raise


def run_main():
    sys.exit(run_main_and_report(configure()))
