#!/usr/bin/env python3

# Copyright (c) 2016 Erik Johansson <erik@ejohansson.se>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307
# USA

from binascii import hexlify
from collections import namedtuple
from datetime import datetime, timedelta

import logging
import math
import select
import socket
import struct
import time


ADDRESS = '239.12.255.255'
PORT = 9522


class MalformedPacketError(Exception):
    pass


def hexdump(data, length=16):
    for offset in range(0, len(data), length):
        segment = data[offset:offset + length]
        hex = ' '.join(["%02x" % b for b in segment])
        text = ''.join([(chr(x).isalnum() and chr(x)) or '.' for x in segment])
        logging.debug("  %02x: %-*s %s" % (offset, 3 * length, hex, text))


class MulticastSocket:
    def __init__(self, address, port, timeout=5):
        self.address = address
        self.port = port

        self.socket = socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
        self.socket.settimeout(timeout)

        self.socket.bind(('', port))
        self.socket.setsockopt(socket.SOL_IP, socket.IP_MULTICAST_LOOP, 0)
        self.socket.setsockopt(
            socket.SOL_IP, socket.IP_ADD_MEMBERSHIP,
            socket.inet_aton(address) + socket.inet_aton('0.0.0.0'))

    def fileno(self):
        return self.socket.fileno()

    def send(self, data):
        logging.debug("Sending %d bytes to %s:%s",
                      len(data), self.address, self.port)
        hexdump(data)
        self.socket.sendto(data, (self.address, self.port))

    def recv(self, bufsize=1500):
        data, peer = self.socket.recvfrom(bufsize)
        logging.debug("Received %d bytes from %s:%s",
                      len(data), peer[0], peer[1])
        hexdump(data)
        return data


DataUnit = namedtuple('DataUnit', ['tag', 'version', 'data'])


class DataPacket:
    """A DataPacket is a sequence of DataUnit's prefixed with a header.

    Each unit consists of a length, a tag (i.e. id), a version (often 0) and
    data bytes. The first tag is "Tag0" with a group id (1 being the default
    according to SMA energy meter protocol documentation). Then follows any
    other units (e.g. SMA net 2). At the end there's the "end" unit.

    """
    HEADER = b'SMA\0'
    TAG_0 = 42
    TAG_NET_2 = 1
    TAG_DISCOVER = 2
    TAG_END = 0
    DEFAULT_GROUP = 1

    def __init__(self, data=None):
        self.units = []
        if data is None:
            self.pack_unit(DataPacket.TAG_0, ">L", DataPacket.DEFAULT_GROUP)
        else:
            if data[0:len(DataPacket.HEADER)] != DataPacket.HEADER:
                raise MalformedPacketError("SMA header incorrect")

            # Unpack units
            offset = len(DataPacket.HEADER)
            header_size = struct.calcsize(">HH")
            while offset + header_size <= len(data):
                (length, tag) = struct.unpack_from(">HH", data, offset)
                offset += header_size
                version = tag & 0xf
                self.add_unit(tag >> 4, data[offset:offset + length], version)
                offset += length
            if offset != len(data):
                raise MalformedPacketError("data unit length missmatch")

            # Check for end unit and drop it
            if (not self.units
                or self.units[-1].tag != DataPacket.TAG_END
                or self.units[-1].data is not None):
                raise MalformedPacketError("missing or malformed end tag")
            self.units.pop()

    def get_data(self, extra_unit=None):
        """Returns the units as a sequence of bytes."""
        units = self.units.copy()
        if extra_unit is not None:
            units.append(extra_unit)
        units.append(DataUnit(DataPacket.TAG_END, 0, None))

        data = DataPacket.HEADER
        for unit in units:
            length = len(unit.data) if unit.data else 0
            tag = unit.tag << 4 | (unit.version & 0xf)
            data += struct.pack(">HH", length, tag)
            if unit.data:
                data += unit.data
        return data

    def add_unit(self, tag, data, version=0):
        self.units.append(DataUnit(tag, version, data if data else None))

    def pack_unit(self, tag, fmt, *args, **kwargs):
        self.add_unit(tag, struct.pack(fmt, *args), **kwargs)


class Net2DataPacket(DataPacket):
    """SMA net 2 data packet.

    Packs data bytes in a net 2 data unit. The data is prefixed with a protocol
    ID. The format of the data depends on the protocol.

    """

    def __init__(self, protocol=None, data=None):
        super().__init__(data=data)

        self.protocol = protocol
        self.net_data = b''

        if data is not None:
            assert(protocol is None)
            # Extract the net 2 unit data
            units = self.units
            self.units = []
            for unit in units:
                if unit.tag == DataPacket.TAG_NET_2:
                    if len(unit.data) < 2:
                        raise MalformedPacketError("too short net 2 data")
                    self.protocol = struct.unpack(">H", unit.data[0:2])[0]
                    self.net_data = unit.data[2:]
                else:
                    self.units.append(unit)
        if self.protocol is None:
            raise MalformedPacketError("no net 2 protocol found")

    def get_data(self):
        data = struct.pack(">H", self.protocol) + self.net_data
        unit = DataUnit(DataPacket.TAG_NET_2, 0, data)
        return super().get_data(unit)


# SUSy ID = SMA update system ID
Address = namedtuple('Address', ['susy_id', 'serial'])
WildcardAddress = Address(0xffff, 0xffffffff)


class DeviceDataPacket:
    PROTOCOL = 24677
    CMD_READ_REQUEST = 0
    CMD_READ_RESPONSE = 1
    CMD_JOB_REQUEST = 12
    CMD_JOB_RESPONSE = 13
    CMD_JOB_LOGOFF = 14

    ReadResponseObject = namedtuple(
        'ReadResponseObject', ['cls', 'code', 'timestamp', 'data'])

    PacketId = 1
    def __init__(self, data=None, **kwargs):
        self.params = []
        self.data = b''

        if data is not None:
            self.packet = Net2DataPacket(data=data)
            if self.packet.protocol != DeviceDataPacket.PROTOCOL:
                raise MalformedPacketError("Not a device data packet")

            if len(self.packet.net_data) < 28:
                raise MalformedPacketError("Too short device data packet")
            v = struct.unpack_from("<BBHLBBHLBBHHHBBH", self.packet.net_data)
            if v[0] * 4 != len(self.packet.net_data):
                raise MalformedPacketError("Wrong device data packet length")
            offset = 28

            self.control = v[1]
            self.destination = Address(v[2], v[3])
            self.job_num = v[5] & 0xf
            self.source = Address(v[6], v[7])
            self.status = v[10]
            self.packet_count = v[11]
            self.packet_id = v[12] & ~0x8000
            self.command = v[13]
            self.obj = v[15]
            if offset + v[14] * 4 > len(self.packet.net_data):
                raise MalformedPacketError("Bad device data packet params")
            for param in range(v[14]):
                self.params.append(struct.unpack_from(
                    "<L", self.packet.net_data, offset)[0])
                offset += 4
            self.data = self.packet.net_data[offset:]
            logging.debug(
                "Got obj=%u command=%u job_num=%u params=%s left=%d data=%d",
                self.obj, self.command, self.job_num, self.params,
                self.packet_count, len(self.data))
            if self.status != 0:
                logging.debug("Status not OK")
        else:
            self.packet = Net2DataPacket(protocol=DeviceDataPacket.PROTOCOL)
            self.control = 0x80 | 0x20
            self.destination = kwargs.get('destination', WildcardAddress)
            self.job_num = kwargs.get('job_num', 0)
            self.source = kwargs.get('source', Address(0, 0))
            self.status = 0
            self.packet_count = 0
            self.packet_id = kwargs.get('packet_id')
            if self.packet_id is None:
                self.packet_id = DeviceDataPacket.PacketId
                DeviceDataPacket.PacketId += 1
            self.command = kwargs.get('command', 0)
            self.obj = kwargs.get('obj', 0)

    def add_param(self, param):
        self.params.append(param)

    def get_data(self):
        assert(len(self.data) % 4 == 0)
        length = 7 + len(self.params) + int(len(self.data) / 4)
        data = struct.pack("<BBHLBBHLBBHHHBBH",
                           length, self.control,
                           self.destination.susy_id, self.destination.serial,
                           0, self.job_num & 0xf,
                           self.source.susy_id, self.source.serial,
                           0, self.job_num & 0xf,
                           self.status, self.packet_count, self.packet_id | 0x8000,
                           self.command, len(self.params), self.obj)
        for param in self.params:
            data += struct.pack("<l", param)
        data += self.data
        self.packet.net_data = data
        return self.packet.get_data()

    def decode_read_response(self):
        offset = 0
        response = []
        while offset + 8 <= len(self.data):
            (cls, code, data_type, timestamp) = struct.unpack_from(
                "<BHBL", self.data, offset)
            offset += 8
            response.append(DeviceDataPacket.ReadResponseObject(
                cls=cls, code=code,
                timestamp=datetime.fromtimestamp(timestamp), data=None))
            logging.debug("Got class=0x%x code=0x%x type=0x%x time=%s",
                          cls, code, data_type, response[-1].timestamp)
            if data_type == 0x10:
                # Text: 32 bytes of NULL terminated string
                (text,) = struct.unpack_from("<32s", self.data, offset)
                offset += len(text)
                response[-1] = response[-1]._replace(
                    data=text.rstrip(b'\0').decode('utf-8'))
                logging.debug(" -> text '%s'", response[-1].data)
            elif data_type == 0x8:
                # Status: eight 32-bit words
                attributes = struct.unpack_from("<LLLLLLLL", self.data, offset)
                offset += 4 * len(attributes)
                response[-1] = response[-1]._replace(data=[])
                for attribute in attributes:
                    tag = attribute & 0xffffff
                    is_set = attribute >> 24
                    if tag == 0xfffffe: # End tag
                        break
                    if is_set:
                        response[-1].data.append(tag)
                    logging.debug(" -> attribute %d = %d", tag, is_set)
            elif self.obj == 0x5400:
                # 64-bit integer
                (value,) = struct.unpack_from("<Q", self.data, offset)
                offset += 8
                if value != 0xffffffffffffffff:
                    response[-1] = response[-1]._replace(data=value)
                logging.debug(" -> value 0x%08x (%d)", value, value)
            elif data_type == 0x0 or data_type == 0x40:
                # Five 32-bit integers
                fmt = 5 * ("L" if data_type == 0x0 else "l")
                values = struct.unpack_from("<%s" % fmt, self.data, offset)
                offset += 4 * len(values)
                response[-1] = response[-1]._replace(data=[])
                for value in values:
                    logging.debug(" -> value 0x%08x (%d)", abs(value), value)
                    if ((data_type == 0x0 and value != 0xffffffff)
                        or (data_type == 0x40 and value != -0x80000000)):
                        response[-1].data.append(value)
                    else:
                        response[-1].data.append(None)
        assert(offset == len(self.data))
        return response


def detect_inverters(sock, timeout=5):
    sock = MulticastSocket(ADDRESS, PORT) if sock is None else sock
    device = DeviceDataPacket(source=Address(189, 123456),
                              command=DeviceDataPacket.CMD_READ_REQUEST)
    device.add_param(0)
    device.add_param(0)
    sock.send(device.get_data())

    #message = DataPacket()
    #message.add_unit(DataPacket.TAG_DISCOVER, None)
    #send_message(sock, message.get_data(), ADDRESS, PORT)

    end = time.time() + timeout
    inverters = []
    while True:
        wait = end - time.time()
        if wait < 0:
            break
        read, _, _ = select.select([sock], [], [], wait)
        if sock in read:
            response = DeviceDataPacket(sock.recv())
            logging.debug("Found inverter type %d with serial %u",
                          response.source.susy_id, response.source.serial)
            inverters.append(Inverter(sock, response.source))
        continue
        if sock in read:
            response = DataPacket(receive_message(sock))
            for unit in response.units:
                if unit.tag == 3:
                    address = socket.inet_ntoa(unit.data)
                    logging.debug("Found inverter on %s", address)
                    inverters.append(Inverter(address))
                    break
    return inverters


class Inverter:
    OBJ_LOGIN = 65533
    JOB_NUM_LOGIN = 1
    JOB_NUM_LOGOUT = 3

    CODE_DAY_YIELD = 0x2622
    CODE_TOTAL_YIELD = 0x2601
    CODE_DC_POWER = 0x251e
    CODE_DC_INPUT_VOLTAGE = 0x451f
    CODE_DC_INPUT_CURRENT = 0x4521
    CODE_AC_POWER = 0x263f
    CODE_AC_POWER_L1 = 0x4640
    CODE_AC_POWER_L2 = 0x4641
    CODE_AC_POWER_L3 = 0x4642
    CODE_AC_VOLTAGE_L1 = 0x4648
    CODE_AC_VOLTAGE_L2 = 0x4649
    CODE_AC_VOLTAGE_L3 = 0x464a
    CODE_AC_CURRENT_L1 = 0x4650
    CODE_AC_CURRENT_L2 = 0x4651
    CODE_AC_CURRENT_L3 = 0x4652

    def __init__(self, sock, address):
        self.socket = sock
        self.address = address
        self.local_address = Address(1, 123456)

    def _to_voltage(self, voltage):
        return voltage / 100.0 if voltage else voltage

    def _to_current(self, current):
        return current / 1000.0 if current else current

    def login(self, user_type, password):
        encoded = bytes(map(lambda x: ord(x) + user_type, password))
        encoded += max(0, 12 - len(password)) * bytes(user_type)
        packet = DeviceDataPacket(
            source=self.local_address,
            destination=self.address,
            command=DeviceDataPacket.CMD_JOB_REQUEST,
            obj=Inverter.OBJ_LOGIN,
            job_num=Inverter.JOB_NUM_LOGIN)
        packet.data = encoded[0:12]
        packet.add_param(7 if user_type == 0x88 else 10)
        packet.add_param(300)
        packet.add_param(int(time.time()))
        packet.add_param(0)

        self.socket.send(packet.get_data())
        response = DeviceDataPacket(self.socket.recv())
        if response.destination == self.local_address:
            logging.info("Login successful")

    def login_user(self, password):
        logging.debug("Logging in as user")
        return self.login(0x88, password)

    def login_installer(self, password):
        logging.debug("Logging in as installer")
        return self.login(0xBB, password)

    def get_day_yield(self):
        packet = DeviceDataPacket(
            source=self.local_address,
            destination=self.address,
            command=DeviceDataPacket.CMD_READ_REQUEST,
            obj=0x5400)
        packet.add_param(0x262200)
        packet.add_param(0x2622FF)

        self.socket.send(packet.get_data())
        response = DeviceDataPacket(self.socket.recv()).decode_read_response()
        assert(len(response) == 1)
        assert(response[0].code == Inverter.CODE_DAY_YIELD)

        energy = response[0].data
        logging.debug("Day yield: %d Wh (%.3f kWh)", energy, energy / 1000.0)
        return energy

    def get_total_yield(self):
        packet = DeviceDataPacket(
            source=self.local_address,
            destination=self.address,
            command=DeviceDataPacket.CMD_READ_REQUEST,
            obj=0x5400)
        packet.add_param(0x260100)
        packet.add_param(0x2601FF)

        self.socket.send(packet.get_data())
        response = DeviceDataPacket(self.socket.recv()).decode_read_response()
        assert(len(response) == 1)
        assert(response[0].code == Inverter.CODE_TOTAL_YIELD)

        energy = response[0].data
        logging.debug("Total yield: %d Wh (%.3f MWh)",
                      energy, energy / 1000.0 / 1000.0)
        return energy

    def get_dc_data(self):
        packet = DeviceDataPacket(
            source=self.local_address,
            destination=self.address,
            command=DeviceDataPacket.CMD_READ_REQUEST,
            obj=0x5380)
        packet.add_param(0x251E00)
        packet.add_param(0x4521FF)

        self.socket.send(packet.get_data())
        response = DeviceDataPacket(self.socket.recv()).decode_read_response()

        power = [None, None]
        voltage = [None, None]
        current = [None, None]

        for r in response:
            assert(r.cls == 1 or r.cls == 2)
            if r.code == Inverter.CODE_DC_POWER:
                power[r.cls - 1] = r.data[0]
            elif r.code == Inverter.CODE_DC_INPUT_VOLTAGE:
                voltage[r.cls - 1] = self._to_voltage(r.data[0])
            elif r.code == Inverter.CODE_DC_INPUT_CURRENT:
                current[r.cls - 1] = self._to_current(r.data[0])

        for i in range(2):
            if power[i] is None:
                logging.debug("DC input %d: no data", i)
            else:
                logging.debug(
                    "DC input %d: %.2f V, %.3f A, %d W (calc %.3f W)",
                    i, voltage[i], current[i], power[i],
                    voltage[i] * current[i])

        return (power, voltage, current)

    def get_ac_data(self):
        packet = DeviceDataPacket(
            source=self.local_address,
            destination=self.address,
            command=DeviceDataPacket.CMD_READ_REQUEST,
            obj=0x5100)
        packet.add_param(0x464000)
        packet.add_param(0x4655ff)

        self.socket.send(packet.get_data())
        response = DeviceDataPacket(self.socket.recv()).decode_read_response()

        power = 3 * [None]
        voltage = 3 * [None]
        current = 3 * [None]

        for r in response:
            if r.code in [Inverter.CODE_AC_POWER_L1,
                          Inverter.CODE_AC_POWER_L2,
                          Inverter.CODE_AC_POWER_L3]:
                power[r.code - Inverter.CODE_AC_POWER_L1] = r.data[0]
            elif r.code in [Inverter.CODE_AC_VOLTAGE_L1,
                            Inverter.CODE_AC_VOLTAGE_L2,
                            Inverter.CODE_AC_VOLTAGE_L3]:
                voltage[r.code - Inverter.CODE_AC_VOLTAGE_L1] = \
                    self._to_voltage(r.data[0])
            elif r.code in [Inverter.CODE_AC_CURRENT_L1,
                            Inverter.CODE_AC_CURRENT_L2,
                            Inverter.CODE_AC_CURRENT_L3]:
                current[r.code - Inverter.CODE_AC_CURRENT_L1] = \
                    self._to_current(r.data[0])

        logging.debug("AC: power %s W, voltage %s V, current %s A",
                      power, voltage, current)
        return (power, voltage, current)

    def get_ac_total_power(self):
        packet = DeviceDataPacket(
            source=self.local_address,
            destination=self.address,
            command=DeviceDataPacket.CMD_READ_REQUEST,
            obj=0x5100)
        packet.add_param(0x263F00)
        packet.add_param(0x263Fff)

        self.socket.send(packet.get_data())
        response = DeviceDataPacket(self.socket.recv()).decode_read_response()
        assert(len(response) == 1)

        power = response[0].data[0]
        if power is not None:
            logging.debug("AC total power: %d W", power)
        else:
            logging.debug("Inverter not active")
        return power

    def get(self):
        logging.debug("Get device name, type etc.")
        packet = DeviceDataPacket(
            source=self.local_address,
            destination=self.address,
            command=DeviceDataPacket.CMD_READ_REQUEST,
            obj=0x5800)
        packet.add_param(0x821e00)
        packet.add_param(0x8220ff)

        self.socket.send(packet.get_data())
        response = DeviceDataPacket(self.socket.recv())
        response.decode_read_response()
        #response = DeviceDataPacket(self.socket.recv())
        #response.decode_read_response()

    def get_day_data(self, start, end):
        packet = DeviceDataPacket(
            source=self.local_address,
            destination=self.address,
            command=DeviceDataPacket.CMD_READ_REQUEST,
            obj=0x7000)
        packet.control = 0xe0;
        packet.add_param(int(start.timestamp()))
        packet.add_param(int(end.timestamp()))

        self.socket.send(packet.get_data())
        while True:
            response = DeviceDataPacket(self.socket.recv())

            offset = 0
            while offset + 12 <= len(response.data):
                (timestamp, energy) = struct.unpack_from(
                    "<LQ", response.data, offset)
                offset += 12
                timestamp = datetime.fromtimestamp(timestamp)
                logging.debug("Yield @ %s: %d Wh (%.3f MWh)",
                              timestamp, energy, energy / 1000.0 / 1000.0)
            assert(offset == len(response.data))
            if response.packet_count == 0:
                break

    def get_month_data(self, start, end):
        packet = DeviceDataPacket(
            source=self.local_address,
            destination=self.address,
            command=DeviceDataPacket.CMD_READ_REQUEST,
            obj=0x7020)
        packet.control = 0xe0;
        packet.add_param(int(start.timestamp()))
        packet.add_param(int(end.timestamp()))

        self.socket.send(packet.get_data())
        while True:
            response = DeviceDataPacket(self.socket.recv())

            offset = 0
            while offset + 12 <= len(response.data):
                (timestamp, energy) = struct.unpack_from(
                    "<LQ", response.data, offset)
                offset += 12
                timestamp = datetime.fromtimestamp(timestamp)
                logging.debug("Yield @ %s: %d Wh (%.3f MWh)",
                              timestamp, energy, energy / 1000.0 / 1000.0)
            assert(offset == len(response.data))
            if response.packet_count == 0:
                break

    def logout(self):
        logging.debug("Logging off")
        packet = DeviceDataPacket(
            source=self.local_address,
            command=DeviceDataPacket.CMD_JOB_LOGOFF,
            obj=Inverter.OBJ_LOGIN,
            job_num=Inverter.JOB_NUM_LOGOUT)
        packet.add_param(-1)
        self.socket.send(packet.get_data())
        #DeviceDataPacket(self.socket.recv())


if __name__ == '__main__':
    logging.basicConfig(format='%(asctime)s %(levelname)s: %(message)s',
                        level=logging.DEBUG)
    #sock = MulticastSocket(ADDRESS, PORT)
    inverter = detect_inverters(sock=None, timeout=1)[0]
    inverter.local_address = Address(1, 654321)
    inverter.login_user("0000")
    inverter.get_day_yield()
    inverter.get_total_yield()
    inverter.get_dc_data()
    inverter.get_ac_data()
    inverter.get_ac_total_power()
    #now = datetime.now()
    #inverter.get_day_data(now - timedelta(hours=5), now)
    #inverter.get_month_data(now - timedelta(days=5), now)
    inverter.get()
    inverter.logout()
