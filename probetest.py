import re
import sys

import serial


params = {
    'port': '/dev/ttyr00',
    'baudrate': 1200,
    'bytesize': 7,
    'parity': 'E',
    'stopbits': 1,
    'timeout': 0.5,
}
probe_address = 0

print('opening port...')
with serial.Serial(**params) as port:
    print(f'finding probe at address {probe_address}...')
    command = f'{probe_address}I!'
    port.write(f'{command}\r\n'.encode())
    probe_id = port.readline()
    probe_id = probe_id.decode().rstrip()
    try:
        _, probe_model_info = probe_id.split('AquaChck', 1)
    except:
        if probe_id:
            sys.exit(f'ERROR: Invalid response from probe: \"{probe_id}\"')
        sys.exit('ERROR: No response from probe')
    probe_model = probe_model_info[:6]
    probe_version = probe_model_info[6:9]
    probe_serial_number = probe_model_info[9:]
    print(f'Aquacheck {probe_model} S/N {probe_serial_number} ver. {probe_version}')

    print('starting moisture measurement...')
    command = f'{probe_address}M0!'
    port.write(f'{command}\r\n'.encode())
    response = port.readline()
    response = response.decode().rstrip()
    delay = int(response[0:3])
    num_sensors = int(response[-1])
    if delay:
        print(f'WAIT: {num_sensors} sensors will be ready in {delay} seconds...')
    attention_response = False
    timeout_cycles = 0
    # if there is a delay indicated, the probe will send \r\n as "attention response"
    # after approx. <delay> seconds to signal that data is ready
    if delay:
        while not attention_response:
            if timeout_cycles * params['timeout'] >= delay:
                break
            attention_response = port.readline()
            timeout_cycles += 1
        if not attention_response:
            print('ERROR: no \"attention response\" from probe, continuing...')
    print('reading moisture data...')
    moisture_values = list()
    error = False
    for r in range(num_sensors):
        command = f'{probe_address}D{r}!'
        port.write(f'{command}\r\n'.encode())
        response = port.readline()
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
                print(f'ERROR: got bad value {value}', flush=True)
                error = True
                continue
            except AssertionError:
                print(f'ERROR: out of range value \"{moisture_value}\"', flush=True)
                error = True
                continue
            moisture_values.append(moisture_value)
    try:
        assert len(moisture_values) == num_sensors
    except AssertionError:
        print(f'ERROR: failed to read {num_sensors - len(moisture_values)} moisture sensors')
        error = True

    print('starting temperature measurement...')
    command = f'{probe_address}M1!'
    port.write(f'{command}\r\n'.encode())
    response = port.readline().decode().rstrip()
    delay = int(response[0:3])  # should be 0, and no attention response when ready
    num_sensors = int(response[-1])
    print('reading temperature data...')
    temperature_values = list()
    error = False
    for r in range(num_sensors):
        command = f'{probe_address}D{r}!'
        port.write(f'{command}\r\n'.encode())
        response = port.readline()
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
                print(f'ERROR: got bad value {value}', flush=True)
                error = True
                continue
            except AssertionError:
                print(f'ERROR: out of range value \"{temperature_value}\"', flush=True)
                error = True
                continue
            temperature_values.append(temperature_value)
    try:
        assert len(temperature_values) == num_sensors
    except AssertionError:
        print(f'ERROR: failed to read {num_sensors - len(temperature_values)} temperature sensors')
        error = True

    # done
    print('cleaning up...')
    port.reset_input_buffer()
if error:
    print('ERROR: failed to read all values, please retry.')
print('SOIL MOISTURE: ', moisture_values)
print('TEMPERATURE:   ', temperature_values)
