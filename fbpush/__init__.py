# Copyright (c) 2015-present, Facebook, Inc.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree. An additional grant
# of patent rights can be found in the PATENTS file in the same directory

import json
import logging
import optparse
import os


class Configure(object):
    OPTION_ATTRS = ['names', 'vendors', 'locations', 'ips']
    LOG_DIR = '/var/tmp/%s/log' % os.environ['USER']

    LOG_LEVELS = {0: logging.ERROR, 1: logging.WARNING,
                  2: logging.INFO, 3: logging.DEBUG}
    LOG_ERROR_VERBOSITY = 0
    LOG_VERBOSITY = LOG_ERROR_VERBOSITY
    LOG_DEBUG_VERBOSITY = 3
    LOG_FILENAME = os.path.join(LOG_DIR, 'fbpush.log')
    LOG_MAX_BYTES = 10000000
    LOG_BACKUP_COUNT = 10
    LOG_FORMAT = '%(asctime)-15s %(levelname)s:%(name)s\
                 [%(processName)s/\
                 %(threadName)s] %(module)s:%(funcName)s.%(lineno)d \
                 %(message)s'

    CURRENT_DIR = os.path.dirname(os.path.realpath(__file__))
    REMINDER_PATH = CURRENT_DIR + '/reminder_msg'
    DRY_RUN_VENDORS = ['juniper']
    STDIN_FNAME = 'stdin-pseudo.conf'
    XNM_SSL_PORT = 3220
    CONFIG_DIR = '/var/lib/fbpush/config/'
    DEFAULT_DEVICE_FILE = 'devices.json'
    XML_DEBUG_FILENAME = os.path.join(LOG_DIR,
                                      'fbpush_xml_debug.log')
    HASH_F_NAME = '/%s_fbpush_dry_run.hash' % os.environ['USER']
    DRY_RUN_HASH_FILENAME = LOG_DIR + HASH_F_NAME

    FBPUSH_SINGLE_FILENAME = 'fbpush.diff'

    def __init__(self):
        self.parser = self._get_option_parser()
        self.config, self.params = self.parser.parse_args()

        if self.config.config_dir:
            setattr(self.__class__, 'CONFIG_DIR', self.config.config_dir)

        try:
            CFG_PATH = self.CONFIG_DIR + 'configuration.json'
            cfg = json.loads(open(CFG_PATH, 'r').read())
        except:
            print("configuration file not found proceeding with defaults")
        else:
            if cfg['configurator']:
                for key, value in cfg['configurator'].iteritems():
                    if value is None:
                        continue
                    if key == 'LOG_LEVELS':
                        cvalue = dict((int(k), v) for k, v in value.items())
                        setattr(self.__class__, key, cvalue)
                    else:
                        setattr(self.__class__, key, value)

        for key in dir(self.__class__):
            if key.startswith('_'):
                continue
            value = getattr(self, key)
            setattr(self.config, key, value)

        setattr(self.config, 'parser', self.parser)

    @staticmethod
    def _opt_split_list(option, opt, value, parser):
        """Callback functions for the add_option method for OptionParser."""

        setattr(parser.values, option.dest, value.split(',') if value else [])

    def __getattr__(self, param):
        """Attribute accessor helper."""
        try:
            return getattr(self, param)
        except:
            raise AttributeError('No attribute named %s' % param)

    def _get_option_parser(self):
        desc = "Parser for fbnet_push tool"
        parser = optparse.OptionParser(description=desc)

        for attr in self.OPTION_ATTRS[:-1]:
            parser.add_option('--' + attr, dest=attr, default=None,
                              type='string', action='callback',
                              callback=self._opt_split_list)

            parser.add_option('--' + attr[:-1] + '_regex',
                              dest=attr[:-1] + '_regex',
                              default=None, type='string')
            parser.add_option('--exclude_' + attr[:-1] + '_regex',
                              dest='exclude_' + attr[:-1] + '_regex',
                              default=None, type='string')

        parser.add_option('--config-dir', dest='config_dir', default=None)

        parser.add_option('-u', '--user', dest='username', default=None)
        parser.add_option('-p', '--pass', dest='password', default=None)
        parser.add_option('-c', '--confirmed_in', type='int',
                          dest='confirmed_in', default=5,
                          help='Do commit confirmed with pending automatic \
                          commit\
                          in ' + 'N minutes. This option is enabled by default\
                          for\
                          % default minutes ' + '(unless -\-dry_run mode is \
                          used)'
                          )
        parser.add_option('-r', '--rollback_in', type='int',
                          dest='rollback_in', default=None,
                          help='Do commit confirmed with pending rollback in N\
                          minutes')
        parser.add_option('-a', '--atomic', action='store_true', dest='atomic',
                          default=False, help="All or nothing")
        parser.add_option('-f', '--force', action='store_true', dest='force',
                          default=False, help='Force any configlet filename\
                          (not only .conf)')
        parser.add_option('--gen_md5', action='store_true', dest='gen_md5',
                          default=False, help='generate md5 for configlets'
                          'the md5 is based on the content EXCLUDE the tag\
                          line')
        parser.add_option('-n', '--dry_run', action='store_true',
                          dest='dry_run',
                          default=False, help="Run everything, but don't\
                          commit changes")
        parser.add_option('-m', '--multiple_diffs', action='store_true',
                          dest='multiple_diffs', default=False,
                          help="Create separate diff file for each router")
        parser.add_option('-x', '--xml', action='store_true',
                          dest='xml', default=False,
                          help='Dump XML transcript of all RPCs executed')
        parser.add_option('-d', '--diff_dir',
                          dest='diff_dir', default=self.LOG_DIR,
                          help="Directory for diff(s) between 'before' and\
                          'after' configuration")
        parser.add_option('--stdin', action='store_true',
                          default=False, dest='stdin',
                          help='Read from stdin')
        parser.add_option('--color', action='store_true', dest='color',
                          default=False, help='highlight for diff regex match')
        parser.add_option('--clowntown', dest='clowntown',
                          default=False, action='store_true',
                          help='By pass dry run check if anything go wrong, \
                          ' 'Use with care as you may cause a SEV')
        parser.add_option('--xml_debug', dest='xml_debug',
                          default=False, action='store_true',
                          help='enable raw XML debug log for junos.')
        parser.add_option('--full_comments', action='store_true',
                          default=False, dest='full_comments',
                          help='Return raw diffs without filtering comments')
        parser.add_option('--no_commit_full', action='store_true',
                          default=False, dest='no_commit_full',
                          help='default is %default. ' 'Only effective on \
                          juniper, use hidden commit-full rpc. ' 'Note that\
                          the commit time will be a little longer when it\
                          is on')
        return parser

configure = Configure()
config, params = configure.config, configure.params
