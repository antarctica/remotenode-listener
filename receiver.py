import argparse
import binascii
import datetime
import inspect
import logging
import os
import pprint
import re
import serial
import serial.threaded
import serial.serialutil as su
import socket
import struct
import subprocess as sp
import sys
import time
import xmodem

from threading import Thread

logging.basicConfig(
    level=logging.DEBUG,
)


class SocatException(Exception):
    pass


# TODO: Would be prettier to decorate from this
class DebugSerial(serial.Serial):
    def __init__(self, *args, **kwargs):
        super(DebugSerial, self).__init__(*args, **kwargs)
        self._filename = datetime.datetime.now().strftime("debug.%d%m%Y-%H%M%S.out")

    def read(self, *args, **kwargs):
        ret = super(DebugSerial, self).read(*args, **kwargs)
        if len(ret) > 1:
            self.output_debug(ret)
        return ret

    def read_until(self, *args, **kwargs):
        ret = super(DebugSerial, self).read_until(*args, **kwargs)
        self.output_debug(ret)
        return ret

    def write(self, data):
        ret = super(DebugSerial, self).write(data)
        self.output_debug(data)
        return ret

    def output_debug(self, msg):
        with open(self._filename, "a") as fh:
            fh.write("""
---
{}
---
{}
---
""".format(msg.decode(), "".join(["{:02X}".format(b) for b in msg])))


class DataReceiver(object):
    def __init__(self, port, location, output_dir, debug=False):
        self._debug = debug
        self._dir = output_dir
        self._port = port
        self._ttyloc = location

        if not os.path.exists(self._dir):
            raise DataReceiverConfigurationError("{} doesn't exist".format(self._dir))

        self._socat = sp.Popen('socat pty,link={},raw tcp-listen:{},fork,reuseaddr'.format(self._ttyloc, self._port),
                            shell=True)

        time.sleep(1)

        self._thread = Thread(target=self.run)
        self._thread.start()

        self._intentional_exit = False

    def run(self):
        ser_port = None

        try:
            while True:
                logging.info('Waiting for connection on {}...'.format(self._port))

                if not ser_port or not ser_port.is_open:
                    cls = serial.Serial
                    if self._debug:
                        cls = DebugSerial
                    ser_port = cls(self._ttyloc,
                                   baudrate=19200,
                                   bytesize=serial.EIGHTBITS,
                                   parity=serial.PARITY_NONE,
                                   stopbits=serial.STOPBITS_ONE,
#                                   timeout=10,
#                                   write_timeout=10,
                                   rtscts=True,
                                   dsrdtr=True
                                   )
                logging.info('Connected to fake serial {}'.format(self._ttyloc))
                ser_port.flushInput()

                data = None
                while not data:
                    data = ser_port.read_until(terminator=su.LF)

                logging.debug("Data received: {}".format(data))

                filename_command = data.decode("latin-1").strip()
                logging.debug("Comparing {} and {}".format(data.decode("ascii").strip(), "FILENAME"))

                # There is often a stray \x00 byte at the start, which I'm happy to fuck off...
                if filename_command.endswith("FILENAME"):
                    logging.debug("Sending FILENAME response...")
                    # TODO: \r\n (gahhh) due to inconsistent line terminators, sort it out
                    ser_port.write("GOFORIT\r\n".encode("latin-1"))
                else:
                    logging.debug("No valid message received, start again...")
                    continue

                logging.info("Waiting for filename information...")

                data = None
                while not data:
                    data = ser_port.read_until(terminator=su.to_bytes([0x1b]))

                logging.debug("File message: {}".format(data))

                try:
                    (lead, length) = struct.unpack_from("BB", data)
                    (filename) = struct.unpack_from("{}s".format(length), data, struct.calcsize("BB"))[0]
                    (crc32, tail) = struct.unpack_from("qB", data,
                                                       struct.calcsize("BB{}s".format(length)))
                except struct.error:
                    raise

                logging.info("Received filename information, checking...")
                logging.debug("Filename length: {}".format(length))
                logging.debug("Filename: {}".format(filename))
                logging.debug("Filename CRC: {}".format(crc32))

                if lead == 0x1a \
                   and binascii.crc32(filename) & 0xffffffff == crc32:
                    ser_port.write("NAMERECV\r\n".encode("latin-1"))

                    def _getc(size, timeout=ser_port.timeout):
                        #logging.debug("READ SIZE: {}".format(size))

                        ser_port.timeout = timeout
                        read = ser_port.read(size=size) or None

                        #logging.debug("READ DATA: {}".format(read))
                        return read

                    def _putc(data, timeout=ser_port.write_timeout):
                        logging.debug("WRITE DATA: {}".format(data))
                        #ser_port.write_timeout = timeout
                        size = ser_port.write(data=data)
                        ser_port.flush()
                        #logging.debug("WRITE SIZE: {}".format(size))
                        return size

                    xfer = xmodem.XMODEM(_getc, _putc)
                    with open(filename, "wb") as fh:
                        xfer.recv(fh, retry=100)
                else:
                    logging.warning("Invalid message received, looping for another listen")
        finally:
            if ser_port is not None and ser_port.is_open:
                ser_port.close()

            try:
                self._socat.terminate()
            except:
                logging.warning("Could not close the socat process, it's either dead or being a mong...")

    @property
    def debug(self):
        return self._debug

    @debug.setter
    def debug(self, val=True):
        self._debug = val

    @property
    def thread(self):
        return self._thread


class DataReceiverConfigurationError(Exception):
    pass


class DataReceiverRuntimeError(Exception):
    pass

if __name__ == '__main__':
    a = argparse.ArgumentParser()
    a.add_argument("-d", "--debug", help="Write a transaction log", action="store_true", default=False)
    a.add_argument("port", help="TCP port to listen on", type=int)
    a.add_argument("ptyLocation", help="pty to feed TCP to")
    a.add_argument("directory", help="Output directory")
    cmd_args = a.parse_args()

    dm = DataReceiver(cmd_args.port, cmd_args.ptyLocation, cmd_args.directory, cmd_args.debug)
    dm.thread.join()
    logging.info("Stopped listening for data...")
