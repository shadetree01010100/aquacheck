import datetime
import re
import time

from nio import GeneratorBlock, Signal
from nio.command import command
from nio.modules.scheduler import Job
from nio.properties import ListProperty, IntProperty, FloatProperty, \
    PropertyHolder, StringProperty, VersionProperty
from nio.util.threading import spawn

import serial


class ConfiguredProbe(PropertyHolder):

    name = StringProperty(title='Name', default='Zone X', order=0)
    port = StringProperty(title='Serial Port', default='/dev/ttyXX', order=1)


@command('read')
@command('current_state')
class Aquacheck(GeneratorBlock):

    # All probes share these interface parameters, no need to confgure
    COM_PARAMS = {
        'baudrate': 1200,
        'bytesize': 7,
        'parity': 'E',
        'stopbits': 1,
        'timeout': 1,
    }

    configured_probes = ListProperty(
        ConfiguredProbe,
        title='Soil Moisture Probes',
        default=[
            {
                'port': '/dev/ttyXX',
                'name': 'Zone X',
            }
        ],
        order=0)
    read_interval = IntProperty(
        title='Read Interval',
        default=360,
        order=1)
    version = VersionProperty('0.1.0')

    def __init__(self):
        super().__init__()
        self.ports = dict()  # {name: port} mapping of configured probes
        self.port_names = dict() #  {name: port_name} mapping of port strings
        self._active = False  # ignore read command while busy
        self._probe_states = dict()  # True: working normally
                                    # False: Port open, bad data
                                    # None: Port closed
        self._reader_jobs = list()  # scheduled jobs
        self._readings = dict()  # internal buffer for readings

    def current_state(self):
        current_state = dict()
        for name, state in self._probe_states.items():
            if state:
                state_repr = 'Ready'
            elif state is None:
                state_repr = 'Interface Error'
            else:
                state_repr = 'Protocol Error'
            current_state[name] = state_repr
        return current_state

    def read(self):
        self.logger.info('Received \"read\" command')
        if self._active:
            self.logger.warning('Busy')
            return
        self._read_and_notify()

    def start(self):
        super().start()
        # schedule a repeatable callback for reads
        delta = datetime.timedelta(seconds=self.read_interval())
        job = Job(self._read_and_notify, delta, True)
        self._reader_jobs.append(job)
        # and take a reading now
        self._read_and_notify()

    def stop(self):
        # cancel jobs
        for job in self._reader_jobs:
            job.cancel()
        super().stop()

    def _read(self, name, port_name):
        params = self.COM_PARAMS.copy()
        params['port'] = port_name
        with serial.Serial(**params) as port:
            # id probe
            command = '0I!\r\n'.encode()
            port.write(command)
            self.logger.debug('[{}] --> {}'.format(name, command))
            response = port.readline()
            self.logger.debug('[{}] <-- {}'.format(name, response))
            response = response.decode().rstrip()
            _, probe_model_info = response.split('AquaChck', 1)
            probe_model = probe_model_info[:6]
            probe_version = probe_model_info[6:9]
            probe_serial_number = probe_model_info[9:]
            self.logger.debug('[{}] Aquacheck {} S/N {} ver. {}'.format(
                name, probe_model, probe_serial_number, probe_version))
            # moisture sensors
            command = '0M0!\r\n'.encode()
            port.write(command)
            self.logger.debug('[{}] --> {}'.format(name, command))
            response = port.readline()
            self.logger.debug('[{}] <-- {}'.format(name, response))
            response = response.decode().rstrip()
            delay = int(response[0:3])
            num_sensors = int(response[-1])
            if delay:
                self.logger.debug(
                    '[{}] Sensors will be ready in {} seconds...'.format(
                        name,
                        delay))
                time.sleep(delay)  # this should be a Job so it can be cancelled
                attention_response = port.readline()
                self.logger.debug('[{}] <-- {}'.format(name, attention_response))
                if not attention_response:
                    self.logger.warning(
                        '[{}] No \"attention response\", continuing...'.format(
                            name))
            self.logger.debug(
                '[{}] Reading {} moisture sensors'.format(name, num_sensors))
            moisture_values = list()
            moisture_error = False
            for r in range(num_sensors):
                command = '0D{}!\r\n'.format(r).encode()
                port.write(command)
                self.logger.debug('[{}] --> {}'.format(name, command))
                response = port.readline()
                self.logger.debug('[{}] <-- {}'.format(name, response))
                response = response.decode().rstrip()
                if not response:
                    break
                values = re.findall('[\+\-][0-9]+\.[0-9]+', response)
                for value in values:
                    try:
                        moisture_value = float(value)
                        assert -5 < moisture_value < 120
                    except ValueError:
                        self.logger.error(
                            '[{}] Invalid moisture value \"{}\"'.format(
                                name,
                                value))
                        moisture_error = True
                        continue
                    except AssertionError:
                        self.logger.error(
                            '[{}] Out of range moisture value \"{}\"'.format(
                                name,
                                moisture_value))
                        moisture_error = True
                        continue
                    moisture_values.append(moisture_value)
            try:
                assert len(moisture_values) == num_sensors
            except AssertionError:
                if not moisture_error:
                    self.logger.error(
                        '[{}] Failed to read {} moisture sensors'.format(
                            name,
                            num_sensors - len(moisture_values)))
                    moisture_error = True
            # temperature sensors
            command = '0M1!\r\n'.encode()
            port.write(command)
            self.logger.debug('[{}] --> {}'.format(name, command))
            response = port.readline()
            self.logger.debug('[{}] <-- {}'.format(name, response))
            response = response.decode().rstrip()
            delay = int(response[0:3])  # should be 0, no attention response
            num_sensors = int(response[-1])
            self.logger.debug(
                '[{}] Reading {} temperature sensors'.format(name, num_sensors))
            temperature_values = list()
            temp_error = False
            for r in range(num_sensors):
                command = '0D{}!\r\n'.format(r).encode()
                port.write(command)
                self.logger.debug('[{}] --> {}'.format(name, command))
                response = port.readline()
                self.logger.debug('[{}] <-- {}'.format(name, response))
                response = response.decode().rstrip()
                if not response:
                    break
                values = re.findall('[\+\-][0-9]+\.[0-9]+', response)
                for value in values:
                    try:
                        temperature_value = float(value)
                        assert -5 < temperature_value < 120
                    except ValueError:
                        self.logger.error(
                            '[{}] Invalid temperature value \"{}\"'.format(
                                name,
                                value))
                        temp_error = True
                        continue
                    except AssertionError:
                        self.logger.error(
                            '[{}] Out of range temperature value \"{}\"'.format(
                                name,
                                temperature_value))
                        temp_error = True
                        continue
                    temperature_values.append(temperature_value)
            try:
                assert len(temperature_values) == num_sensors
            except AssertionError:
                if not temp_error:
                    self.logger.error(
                        '[{}] Failed to read {} temperature sensors'.format(
                            name,
                            num_sensors - len(temperature_values)))
                    temp_error = True
            self._readings[name] = {
                    'moisture_values': moisture_values,
                    'temperature_values': temperature_values,
                }
            if moisture_error or temp_error:
                self._set_probe_state(name, False)
            else:
                self._set_probe_state(name, True)

    def _set_probe_state(self, name, state):
        try:
            previous_state = self._probe_states[name]
            state_change = state != previous_state
        except KeyError:
            # this is the initial state
            state_change = True
        if state_change:
            self._probe_states[name] = state
            if state:
                self.logger.info('[{}] Status: Ready'.format(name))
            elif state is None:
                self.logger.warning('[{}] Status: Interface Error'.format(name))
            else:
                self.logger.warning('[{}] Status: Protocol Error'.format(name))

    def _read_and_notify(self):
        self._active = True
        self._readings = dict()  # clear internal buffer
        self._spawn_readers()
        signal_list = list()
        for name, reading in self._readings.items():
            signal = Signal({
                'name': name,
                'moisture_values': reading['moisture_values'],
                'temperature_values': reading['temperature_values'],
            })
            signal_list.append(signal)
        self.notify_signals(signal_list)
        self._active = False

    def _spawn_readers(self):
        reader_threads = list()
        for name, port in self.ports.items():
            thread = spawn(self._read, name, port)
            reader_threads.append(thread)
        for thread in reader_threads:
            try:
                thread.join()
            except Exception as e:
                # log errors from worker threads
                self.logger.warning('worker thread raised {}'.format(
                    e.__class__.__name__))
