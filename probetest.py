import re
import sys
import time

import serial


params = {
    'port': '/dev/ttyr00',
    'baudrate': 1200,
    'bytesize': 7,
    'parity': 'E',
    'stopbits': 1,
    'timeout': 1.0,
}

def elapsed(start_time):
    return '{:.3f}'.format(time.monotonic() - start_time)


start_time = time.monotonic()
print('opening port...')
with serial.Serial(**params) as port:
    print('finding probe...')
    command = '0I!\r\n'.encode()
    print('\t{} --> {}'.format(elapsed(start_time), command))
    port.write(command)
    response = port.readline()
    print('\t{} <-- {}'.format(elapsed(start_time), response))
    response = response.decode().rstrip()
    try:
        _, probe_model_info = response.split('AquaChck', 1)
    except:
        if response:
            sys.exit('ERROR: Invalid response from probe: \"{}\"'.format(response))
        sys.exit('ERROR: No response from probe')
    probe_model = probe_model_info[:6]
    probe_version = probe_model_info[6:9]
    probe_serial_number = probe_model_info[9:]
    print('Aquacheck {} S/N {} ver. {}'.format(
        probe_model, probe_serial_number, probe_version))

    print('starting moisture measurement...')
    command = '0M0!\r\n'.encode()
    print('\t{} --> {}'.format(elapsed(start_time), command))
    port.write(command)
    response = port.readline()
    print('\t{} <-- {}'.format(elapsed(start_time), response))
    response = response.decode().rstrip()
    delay = int(response[0:3])
    num_sensors = int(response[-1])
    # if there is a delay indicated, the probe will send \r\n as "attention response"
    # after approx. <delay> seconds to signal that data is ready
    if delay:
        attention_response = False
        print('WAIT: {} sensors will be ready in {} seconds...'.format(
            num_sensors, delay))
        time.sleep(delay)
        attention_response = port.readline()
        print('\t{} <-- {}'.format(
            elapsed(start_time), attention_response), flush=True)
        # while not attention_response:
        #     if timeout_cycles * params['timeout'] >= delay:
        #         break
        #     attention_response = port.readline()
        #     if attention_response:
        #         print('\t{} <-- {}'.format(
        #             elapsed(start_time), attention_response), flush=True)
        #     timeout_cycles += 1
        if not attention_response:
            print('ERROR: no \"attention response\" from probe, continuing...')
    print('reading moisture data...')
    moisture_values = list()
    error = False
    for r in range(num_sensors):
        command = '0D{}!\r\n'.format(r).encode()
        print('\t{} --> {}'.format(
            elapsed(start_time), command), flush=True)
        port.write(command)
        response = port.readline()
        print('\t{} <-- {}'.format(elapsed(start_time), response), flush=True)
        response = response.decode().rstrip()
        if not response:
            break
        values = re.findall('[\+\-][0-9]+\.[0-9]+', response)
        for value in values:
            if not value:
                continue
            try:
                moisture_value = float(value)
                assert -5 < moisture_value < 120
            except ValueError:
                if not value.isprintable():
                    value = value.encode()
                print('ERROR: got bad value {}'.format(value), flush=True)
                error = True
                continue
            except AssertionError:
                print('ERROR: out of range value \"{}\"'.format(moisture_value), flush=True)
                error = True
                continue
            moisture_values.append(moisture_value)
    try:
        assert len(moisture_values) == num_sensors
    except AssertionError:
        print('ERROR: failed to read {} moisture sensors'.format(
            num_sensors - len(moisture_values)))
        error = True

    print('starting temperature measurement...')
    command = '0M1!\r\n'.encode()
    print('\t{} --> {}'.format(elapsed(start_time), command))
    port.write(command)
    response = port.readline()
    print('\t{} <-- {}'.format(elapsed(start_time), response))
    response = response.decode().rstrip()
    delay = int(response[0:3])  # should be 0, and no attention response when ready
    num_sensors = int(response[-1])
    print('reading temperature data...')
    temperature_values = list()
    error = False
    for r in range(num_sensors):
        command = '0D{}!\r\n'.format(r).encode()
        print('\t{} --> {}'.format(elapsed(start_time), command))
        port.write(command)
        response = port.readline()
        print('\t{} <-- {}'.format(elapsed(start_time), response))
        response = response.decode().rstrip()
        if not response:
            break
        values = re.findall('[\+\-][0-9]+\.[0-9]+', response)
        for value in values:
            if not value:
                continue
            try:
                temperature_value = float(value)
                assert -5 < temperature_value < 120
            except ValueError:
                if not value.isprintable():
                    value = value.encode()
                print('ERROR: got bad value {}'.format(value), flush=True)
                error = True
                continue
            except AssertionError:
                print('ERROR: out of range value \"{}\"'.format(temperature_value), flush=True)
                error = True
                continue
            temperature_values.append(temperature_value)
    try:
        assert len(temperature_values) == num_sensors
    except AssertionError:
        print('ERROR: failed to read {} temperature sensors'.format(num_sensors - len(temperature_values)))
        error = True

    # done
    # print('cleaning up...')
    # port.reset_input_buffer()
if error:
    print('ERROR: failed to read all values, please retry.')
print('SOIL MOISTURE: ', moisture_values)
print('TEMPERATURE:   ', temperature_values)
