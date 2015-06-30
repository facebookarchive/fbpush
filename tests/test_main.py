from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals
import mock
import os
import re
import time
import unittest

from fbpush import main, config

# Uncomment if you don't want to see the print messages
# in main.py printed during the run of unittest suite
# sys.stdout = open(os.devnull, 'w')


class FBPushMainTestCase(unittest.TestCase):

    def test_get_device(self):
        config.CONFIG_DIR = os.getcwd()
        config.DEFAULT_DEVICE_FILE = '/tests/test_sample.json'

        # Checks if filter based on exact name match works
        config.names = ['abc1']
        devices = main.get_device()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].name, 'abc1')

        # Using assert to check regex match everywhere because
        # assertRegexpMatches is not supported by python2.6
        # Checks if filter based on regex match works
        config.name_regex = '.*[1]+.*'
        devices = main.get_device()
        self.assertEqual(len(devices), 1)
        assert re.match(config.name_regex, devices[0].name) is not None

        # Checks if the filter based on exclude regex works
        # In this case there are two devices one with
        # role1 and other with role2. Since both get filtered
        # the length devices list should be zero.
        config.exclude_name_regex = '.*[1,2]+.*'
        devices = main.get_device()
        self.assertEqual(len(devices), 0)

        # Combination of the above filters
        config.exclude_name_regex = None
        config.names = ['abc1']
        devices = main.get_device()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].name, 'abc1')

    def test_network_filter_get_devices(self):
        config.CONFIG_DIR = os.getcwd()
        config.DEFAULT_DEVICE_FILE = '/tests/test_sample.json'

        # Tests if the exclude regex filter works
        device_filter = {u'exclude_name_regex': 'abc1'}
        devices = main.network_filter(device_filter)
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].name, 'abc2')
        assert re.match('abc1', devices[0].name) is None

        # Tests if the regex filter works
        device_filter = {u'name_regex': 'abc1'}
        devices = main.network_filter(device_filter)
        self.assertEqual(len(devices), 1)
        assert re.match('abc1', devices[0].name) is not None

        # Tests if the exact match filter works
        # Returns all the devices in the test_sample.json(2)
        device_filter = {u'names': ['abc1', 'abc2']}
        devices = main.network_filter(device_filter)
        self.assertEqual(len(devices), 2)

        # Combination of the above filters
        device_filter = {u'names': ['abc1'], u'exclude_name_regex': 'abc1'}
        devices = main.network_filter(device_filter)
        self.assertEqual(len(devices), 0)

    @mock.patch('fbpush.main.sys')
    @mock.patch('fbpush.main.init_logging')
    @mock.patch('fbpush.main.open', create=True)
    def test_configure(self, mock_open, mock_init_logging, mock_sys):
        # if the name of the file doesnot have a .conf ending or http
        # starting this should exit
        main.params = ["test"]
        main.configure()
        mock_sys.exit.assert_called_with(1)

        main.params = ["sample.conf"]

        # The return value of configure should be equal to the config.params
        # Also check if the init_logging is initialized
        rparams = main.configure()
        self.assertEqual(rparams, main.params)
        mock_init_logging.assert_called_with()

        # if config.xml_debug is True the config.xml_debug_fh file descriptor
        # should be present too
        config.xml_debug = True
        main.configure()
        mock_open.assert_called_with(config.XML_DEBUG_FILENAME, 'w', 0)
        config.xml_debug = None

        # If config.dry_run is True the config.confirmed_in flag should be
        # set to None
        config.dry_run = True
        main.configure()
        self.assertEqual(config.confirmed_in, None)

        # If both config.dry_run anf config.confirmed_in flag are both set to
        # True the sys.exit(1) must be called
        config.dry_run = True
        config.confirmed_in = True
        main.configure()
        mock_sys.exit.assert_called_with(1)

        # If config.dry_run is None and config.confirmed_in is True
        # and config.cofirmed_in >= config.rollback_in the program should exit
        config.confirmed_in = True
        config.dry_run = None
        config.rollback_in = time.time()
        config.confirmed_in = config.rollback_in + 1
        main.configure()
        mock_sys.exit.assert_called_with(1)

        # If the above scenario the config.rollback_in is None the else
        # statment must be called
        config.rollback_in = None
        main.configure()
        self.assertEqual(config.rollback_in, config.confirmed_in + 1)

    @mock.patch('fbpush.main.open', create=True)
    @mock.patch('fbpush.json.loads')
    def test_print_reminder(self, mock_json, mock_open):
        # Testing the print_reminder method of the Printreminder class
        main.PrintReminder.yellow = mock.MagicMock()
        main.PrintReminder.red = mock.MagicMock()

        reminder = main.PrintReminder()
        reminder.print_reminder(False, 2, 'test')

        # The open method must be called to load the REMINDER_MSG
        # Also the json.loads must be called.
        mock_open.assert_called_with(config.REMINDER_PATH, 'r')
        self.assertEqual(mock_json.call_count, 1)
        self.assertEqual(3, main.PrintReminder.yellow.call_count)

        # If its not a dry run self.red must be called with the args
        # similar to what print_reminder was called with
        args = (2, 'test')
        msg = '\n- This is NOT DRY_RUN! (%d devices, diff_dir=%s)' % args
        main.PrintReminder.red.assert_called_with(msg)

    @mock.patch('fbpush.main.open', create=True)
    @mock.patch('fbpush.main.sorted', create=True)
    def test_is_dry_run_prev(self, mock_sorted, mock_open):

        # Checks the is_dry_run_prev method. If the md5 hashes
        # not equal False should be returned
        dry_run_bool = main.is_dry_run_prev('test_configlet')
        mock_open.assert_called_with(config.DRY_RUN_HASH_FILENAME, 'r')
        self.assertEqual(mock_sorted.call_count, 1)
        self.assertEqual(dry_run_bool, False)

        # If there is an exception the method should return False
        err = ImportError('abc')
        mock_sorted.side_effect = err
        dry_run_bool = main.is_dry_run_prev('test_configlet')
        self.assertEqual(dry_run_bool, False)

    @mock.patch('fbpush.main.open', create=True)
    @mock.patch('fbpush.main.sorted', create=True)
    def test_update_dry_run_hash(self, mock_sorted, mock_open):

        # Checks the flow of update_dry_run_hash and whether it
        # calls open method and sorted
        main.update_dry_run_hash('test_configlet')
        mock_open.assert_called_with(config.DRY_RUN_HASH_FILENAME, 'w')
        self.assertEqual(mock_sorted.call_count, 1)

        # If no argument supplied should raise an exception
        self.assertRaises(Exception, main.update_dry_run_hash)

    @mock.patch('fbpush.main.sys')
    @mock.patch('fbpush.main.requests', create=True)
    def test_get_file_from_http(self, mock_request, mock_sys):

        # Checks the flow of get_file_from_http method and if
        # the request.get called correctly
        mock_request.get = mock.MagicMock()
        mock_request.codes.ok = mock_request.get().status_code
        main.get_file_from_http('test_url')
        mock_request.get.assert_called_with('test_url', timeout=300)

        # If request.codes.ok and req.status_code are not equal
        # the system should exit
        mock_request.get().status_code = mock.MagicMock()
        main.get_file_from_http('test_url')
        mock_sys.exit.assert_called_with(-1)

        # In normal workflow the method should return value equal to
        # ret.content.splitlines(True)
        mock_request.get().content.splitlines.return_value = 'test_value'
        mock_request.codes.ok = mock_request.get().status_code
        ret = main.get_file_from_http('test_url')
        self.assertEqual(ret, 'test_value')

    @mock.patch('fbpush.main.get_file_from_http')
    @mock.patch('fbpush.main.open', create=True)
    def test_get_configlets(self, mock_open, mock_get):
        # Checks the flow of get_configlets method
        ret = mock.MagicMock()
        main.Configlet = ret
        main.Configlet.return_value = ret
        mock_get.return_value = 'abc'

        # If arg is http//* the code should go through first part of
        # if statement and should return a set of lists
        funame = 'http://test'
        lx = main.get_configlets(funame)
        main.Configlet.assert_called_With(funame, 'abc')
        self.assertEqual(lx, set([ret]))

        # If arg is *.conf the code should go through second part of
        # if statement and should return a set of lists
        funame = 'test.conf'
        mock_open().readlines.return_value = 'abc'
        lx = main.get_configlets(funame)
        main.Configlet.assert_called_with(funame, 'abc')
        self.assertEqual(lx, set([ret]))

if __name__ == '__main__':
    unittest.main()
