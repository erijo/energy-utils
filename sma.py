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
from datetime import datetime

import logging
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
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
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

    def get_data(self, extra_units=[]):
        units = self.units + extra_units
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
    def __init__(self, protocol=None, data=None):
        super().__init__(data=data)
        self.protocol = protocol
        self.net_data = b''

        if data is not None:
            assert(protocol is None)
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
        return super().get_data(extra_units=[unit])


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
            logging.debug("Got obj=%u command=%u job_num=%u params=%s data=%d",
                          self.obj, self.command, self.job_num, self.params,
                          len(self.data))
            if self.status != 0:
                logging.debug("Status not OK")
        else:
            self.packet = Net2DataPacket(protocol=DeviceDataPacket.PROTOCOL)
            self.control = 0x80 | 0x20
            self.destination = kwargs.get('destination', WildcardAddress)
            self.job_num = kwargs.get('job_num', 0)
            self.source = kwargs.get('source', Address(0, 0))
            self.status = 0
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
                           self.status, 0, self.packet_id | 0x8000,
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
            response.append((cls, code, datetime.fromtimestamp(timestamp)))
            logging.debug("Got class=0x%x code=0x%x type=0x%x time=%s",
                          cls, code, data_type, response[-1][2])
            if data_type == 0x10:
                # Text: 32 bytes of NULL terminated string
                (text,) = struct.unpack_from("<32s", self.data, offset)
                offset += len(text)
                response[-1] += (text.rstrip(b'\0').decode('utf-8'),)
                logging.debug(" -> text '%s'", response[-1][3])
            elif data_type == 0x8:
                # Status: eight 32-bit words
                attributes = struct.unpack_from("<LLLLLLLL", self.data, offset)
                offset += 4 * len(attributes)
                response[-1] += ([],)
                for attribute in attributes:
                    tag = attribute & 0xffffff
                    is_set = attribute >> 24
                    if tag == 0xfffffe: # End tag
                        break
                    if is_set:
                        response[-1][3].append(tag)
                    logging.debug(" -> attribute %d = %d", tag, is_set)
            elif self.obj == 0x5400:
                # 64-bit integear
                (value,) = struct.unpack_from("<Q", self.data, offset)
                offset += 8
                response[-1] += (value,)
                logging.debug(" -> value 0x%08x (%d)", value, value)
            elif data_type == 0x0 or data_type == 0x40:
                # Five signed 32-bit integears
                fmt = 5 * ("L" if data_type == 0x0 else "l")
                values = struct.unpack_from("<%s" % fmt, self.data, offset)
                offset += 4 * len(values)
                response[-1] += (values,)
                for value in values:
                    logging.debug(" -> value 0x%08x (%d)", abs(value), value)
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

    def __init__(self, sock, address):
        self.socket = sock
        self.address = address
        self.local_address = Address(1, 123456)

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
        logging.debug("Got day yield %d Wh", response[0][3])
        return response[0][3]

    def get_dc_voltage(self):
        packet = DeviceDataPacket(
            source=self.local_address,
            destination=self.address,
            command=DeviceDataPacket.CMD_READ_REQUEST,
            obj=0x5380)
        packet.add_param(0x451F00)
        packet.add_param(0x451Fff)

        self.socket.send(packet.get_data())
        response = DeviceDataPacket(self.socket.recv()).decode_read_response()
        assert(len(response) == 2)
        assert(response[0][0] == 1)
        voltage = response[0][3][0]
        voltage = 0 if voltage == -2147483648 else voltage
        logging.debug("Got voltage %d", voltage)
        return voltage

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
        power = response[0][3][0]
        power = 0 if power == -2147483648 else power
        logging.debug("Got spot power %d W", power)
        return power

    def get(self):
        logging.debug("Get device name, type etc.")
        packet = DeviceDataPacket(
            source=self.local_address,
            destination=self.address,
            command=DeviceDataPacket.CMD_READ_REQUEST,
            obj=0x5800)
        packet.add_param(0x821E00)
        packet.add_param(0x8220FF)

        self.socket.send(packet.get_data())
        response = DeviceDataPacket(self.socket.recv())
        response.decode_read_response()

    def get2(self):
        logging.debug("Get energy total, today")
        packet = DeviceDataPacket(
            source=self.local_address,
            destination=self.address,
            command=DeviceDataPacket.CMD_READ_REQUEST,
            obj=0x5400)
        packet.add_param(0x260100)
        packet.add_param(0x2622FF)

        self.socket.send(packet.get_data())
        response = DeviceDataPacket(self.socket.recv())
        response.decode_read_response()

    def get3(self):
        packet = DeviceDataPacket(
            source=self.local_address,
            destination=self.address,
            command=DeviceDataPacket.CMD_READ_REQUEST,
            obj=0x5380)
        packet.add_param(0x251E00)
        packet.add_param(0x251EFF)

        self.socket.send(packet.get_data())
        response = DeviceDataPacket(self.socket.recv())
        response.decode_read_response()

    def get4(self):
        packet = DeviceDataPacket(
            source=self.local_address,
            destination=self.address,
            command=DeviceDataPacket.CMD_READ_REQUEST,
            obj=0x5400)
        packet.add_param(0x00000000)
        packet.add_param(0x00ffffff)

        self.socket.send(packet.get_data())
        response = DeviceDataPacket(self.socket.recv())
        response.decode_read_response()

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
    inverter.local_address = Address(1, 123456)
    inverter.login_user("0000")
    inverter.get_day_yield()
    inverter.get_dc_voltage()
    inverter.get_ac_total_power()
    #inverter.get()
    #inverter.get2()
    #inverter.get3()
    #inverter.get4()
    inverter.logout()
    #inverter.get()