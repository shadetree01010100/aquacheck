from unittest.mock import call, MagicMock, patch

from nio import Signal
from nio.testing.block_test_case import NIOBlockTestCase

from ..aquacheck_block import Aquacheck




@patch('serial.Serial')
class TestAquacheck(NIOBlockTestCase):

    def test_aquacheck_reading(self, mock_serial):
        """ Aquacheck probes are polled at a configured interval."""

        mock_port_1 = MagicMock()
        mock_port_1.name = '/dev/ttyTZ1'
        mock_port_2 = MagicMock(name='/dev/ttyTZ2')
        mock_port_2.name = '/dev/ttyTZ2'
        mock_serial.side_effect = [
            mock_port_1,
            mock_port_2,
        ]
        # 6 sensor probe, with variable number format
        mock_port_1.readline.side_effect = [
            '013AquaChckACHSDI043S012345\r\n'.encode('utf-8'),  # id command
            '0036\r\n'.encode('utf-8'),  # moisture read command
            ''.encode('utf-8'),  # waiting
            ''.encode('utf-8'),  # waiting
            '\r\n'.encode('utf-8'),  # attention response
            '+112.12345-4.689'.encode('utf-8'),  # 2 moisture values
            '+32.0000+44.000+99.00+100.2'.encode('utf-8'),  # 4 values
            '\r\n'.encode('utf-8'),  # no more values
            '0006\r\n'.encode('utf-8'),  # temperature read command
            '+22.1234'.encode('utf-8'),  # 1 temp value
            '-2.689+22.0000+24.000+29.00+20.2'.encode('utf-8'),  # 5 values
            '\r\n'.encode('utf-8'),  # no more values
        ]
        # 4 sensor probe
        mock_port_2.readline.side_effect = [
            '013AquaChckACHSDI043S543210\r\n'.encode('utf-8'),
            '0054\r\n'.encode('utf-8'),
            ''.encode('utf-8'),
            ''.encode('utf-8'),
            '\r\n'.encode('utf-8'),
            '-1.234+2.345+3.456'.encode('utf-8'),
            '+4.567,'.encode('utf-8'),
            '\r\n'.encode('utf-8'),
            '0004\r\n'.encode('utf-8'),
            '+9.876+8.765+7.654'.encode('utf-8'),
            '+6.543'.encode('utf-8'),
            '\r\n'.encode('utf-8'),
        ]

        block = Aquacheck()
        self.configure_block(
            block,
            {
                'configured_probes': [
                    {
                        'name': 'Test Zone 1',
                        'port': '/dev/ttyTZ1',
                    },
                                        {
                        'name': 'Test Zone 2',
                        'port': '/dev/ttyTZ2',
                    },
                ],
                'read_interval': 180,
                'log_level': 'DEBUG',
            }
        )
        self.assertEqual(block.read_interval.value, 180)
        self.assertEqual(
            mock_serial.call_args_list,
            [
                call(
                    baudrate=1200,
                    bytesize=7,
                    parity='E',
                    port='/dev/ttyTZ1',
                    stopbits=1,
                    timeout=1),
                call(
                    baudrate=1200,
                    bytesize=7,
                    parity='E',
                    port='/dev/ttyTZ2',
                    stopbits=1,
                    timeout=1),
            ])
        self.assertDictEqual(
            block._probe_states,
            {
                'Test Zone 1': True,
                'Test Zone 2': True,
            })

        block.start()
        self.assert_last_signal_list_notified([
            Signal({
                'name': 'Test Zone 1',
                'moisture_values': [
                    112.12345,
                    -4.689,
                    32.0000,
                    44.000,
                    99.00,
                    100.2,
                ],
                'temperature_values': [
                    22.1234,
                    -2.689,
                    22.0000,
                    24.000,
                    29.00,
                    20.2,
                ],
            }),
            Signal({
                'name': 'Test Zone 2',
                'moisture_values': [
                    -1.234,
                    2.345,
                    3.456,
                    4.567,
                ],
                'temperature_values': [
                    9.876,
                    8.765,
                    7.654,
                    6.543,
                ],
            }),
        ])

        block.stop()
        for name, port in block.ports.items():
            self.assertEqual(port.close.call_count, 1)
