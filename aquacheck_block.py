import datetime
import re

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
                'name': 'Zone ',
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
        self.probes = dict()  # {name: port} mapping of configured probes
        self._active = False  # ignore read command while busy
        self._probe_states = dict()  # True: working normally
                                    # False: Port open, bad data
                                    # None: Port closed
        self._reader_jobs = list()  # scheduled jobs
        self._readings = dict()  # internal buffer for readings

    def configure(self, context):
        super().configure(context)
        connection_threads = list()
        for probe in self.configured_probes():
            thread = spawn(self._open_port, probe.name(), probe.port())
            connection_threads.append(thread)
        for thread in connection_threads:
            thread.join()  # let's hope this returns quickly lmao

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
        # close ports
        for name, port in self.probes.items():
            port.close()
            self.logger.debug('[{}] Closed port {}'.format(name, port.name))
        super().stop()

    def _open_port(self, name, port_name):
        params = self.COM_PARAMS.copy()
        params['port'] = port_name
        try:
            port = serial.Serial(**params)
        except:
            self.logger.error('[{}] Failed to open serial port {}'.format(
                name,
                port_name))
            self._set_probe_state(name, None)
            return
        port.write('0I!\r\n'.encode('utf-8'))
        probe_id = port.readline()
        probe_id = probe_id.decode().rstrip()
        try:
            probe_model_info = probe_id.split('AquaChck')[-1]
        except:
            if probe_id:
                self.logger.error('[{}] Invalid response: \"{}\"'.format(
                    name,
                    probe_id))
            else:
                self.logger.error('[{}] No response from probe'.format(name))
            self._set_probe_state(probe.name(), False)
            return
        probe_version = probe_model_info[6:9]
        probe_serial_number = probe_model_info[10:]
        self.logger.debug(
            '[{}] Ready on {} (S/N {} v.{})'.format(
                name,
                port_name,
                probe_serial_number,
                probe_version))
        self.probes[name] = port
        self._set_probe_state(name, True)

    def _read(self, name, port):
        # moisture sensors
        port.write('0M0!\r\n'.encode('utf-8'))
        response = port.readline()
        response = response.decode().rstrip()
        delay = int(response[0:3])
        num_sensors = int(response[-1])
        if delay:
            self.logger.debug(
                '[{}] {} sensors will be ready in {} seconds...'.format(
                    name,
                    num_sensors,
                    delay))
            time_waited = 0
            while time_waited < delay:
                attention_response = port.readline()
                if attention_response:
                    break
                time_waited += 1
            else:
                self.logger.warning(
                    '[{}] No \"attention response\", continuing...'.format(
                        name))
        moisture_values = list()
        moisture_error = False
        for r in range(num_sensors):
            port.write('0D{}!\r\n'.format(r).encode('utf-8'))
            response = port.readline()
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
                    '[{}] Failed to read all moisture sensors'.format(name))
                moisture_error = True
        # temperature sensors
        port.write('0M1!\r\n'.encode('utf-8'))
        response = port.readline()
        response = response.decode().rstrip()
        delay = int(response[0:3])  # should be 0, no attention response
        num_sensors = int(response[-1])
        temperature_values = list()
        temp_error = False
        for r in range(num_sensors):
            port.write('OD{}!\r\n'.format(r).encode('utf-8'))
            response = port.readline()
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
                    '[{}] Failed to read all temperature sensors'.format(name))
                error = True
        if moisture_error or temp_error:
            self._set_probe_state(name, False)
            return
        self._readings[name] = {
                'moisture_values': moisture_values,
                'temperature_values': temperature_values,
            }
        self._set_probe_state(name, True)

    def _set_probe_state(self, name, state):
        previous_state = self._probe_states.get(name)
        if state != previous_state:
            self._probe_states[name] = state
            if state:
                self.logger.info('[{}] Ready'.format(name))
            elif state is None:
                self.logger.warning('[{}] Interface Error'.format(name))
            else:
                self.logger.warning('[{}] Protocol Error'.format(name))

    def _read_and_notify(self):
        self._active = True
        self._readings = dict()  # clear internal buffer
        try:
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
        except:
            # catch ang log so active flag can be reset
            self.logger.error('Unexpected Error!')
        self._active = False

    def _spawn_readers(self):
        open_threads = list()
        for name, state in self._probe_states.items():
            if state is None:
                thread = spawn(self._open_port, name, self.probes[name].name)
                open_threads.append(thread)
        for thread in open_threads:
            thread.join()
        reader_threads = list()
        for name, port in self.probes.items():
            thread = spawn(self._read, name, port)
            reader_threads.append(thread)
        for thread in reader_threads:
            thread.join()