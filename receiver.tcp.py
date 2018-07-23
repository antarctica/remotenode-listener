import argparse
import binascii
import datetime
import logging
import os
import pprint
import re
import serial
import serial.threaded
import socket
import struct
import sys
import time
import xmodem

from subprocess import Popen, PIPE
from threading import Thread

from pyremotenode.utils import setup_logging

log = setup_logging(__name__, filelog=False)
logging.getLogger().setLevel(logging.DEBUG)
logging.info("PyRMDataReceiver")


# Based on https://github.com/pyserial/pyserial/blob/master/examples/tcp_serial_redirect.py
class DataReceiver(object):
    def __init__(self, port, output_dir):
        self._dir = output_dir
        self._port = port

        if not os.path.exists(self._dir):
            raise DataReceiverConfigurationError("{} doesn't exist".format(self._dir))

        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(('', self._port))
        self._srv.listen(0)

        self._thread = Thread(target=self.run)
        self._thread.start()

        self._intentional_exit = False

    def run(self):
        try:
            while True:
                logging.info('Waiting for connection on {}...'.format(self._port))
                client_socket, addr = self._srv.accept()
                logging.info('Connected by {}'.format(addr))
                # More quickly detect bad clients who quit without closing the
                # connection: After 1 second of idle, start sending TCP keep-alive
                # packets every 1 second. If 3 consecutive keep-alive packets
                # fail, assume the client is gone and close the connection.
                try:
                    client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 1)
                    client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 1)
                    client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
                    client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                except AttributeError:
                    pass  # XXX not available on windows

                client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

                try:
                    while True:
                        try:
                            data = client_socket.recv(4096)
                            if not data:
                                break

                            logging.debug("Data received: {}".format(data.decode("ascii")))

                            if data.decode().strip() == "FILENAME":
                                logging.debug("Sending FILENAME response...")
                                client_socket.send("GOFORIT\r\n".encode("ascii"))
                            else:
                                logging.debug("No valid message received, start again...")
                                continue

                            logging.info("Waiting for filename information...")

                            data = client_socket.recv(4096)
                            if not data:
                                break
                            else:
                                logging.debug("File message: {}".format(data))

                            try:
                                (lead, length) = struct.unpack_from("BB", data)
                                (filename) = struct.unpack_from("{}s".format(length), data, struct.calcsize("BB"))[0]
                                (crc32, tail) = struct.unpack_from("qB", data,
                                                                   struct.calcsize("BB{}s".format(length)))
                            except struct.error:
                                raise

                            logging.info("Received filename infromation, checking...")
                            logging.debug("Filename length: {}".format(length))
                            logging.debug("Filename: {}".format(filename))
                            logging.debug("Filename CRC: {}".format(crc32))

                            if lead == 0x1a and tail == 0x1b \
                               and binascii.crc32(filename) & 0xffffffff == crc32:
                                client_socket.send("NAMERECV\r\n".encode("ascii"))

                                with open("dataout.bin", "wb") as dataout:
                                    def _getc(size, timeout=1):
                                        read = ""
                                        while read == "":
                                            read = client_socket.recv(size)
                                        dataout.write(read)
                                        return read

                                    def _putc(msg, timeout=1):
                                        logging.debug("WRITE DATA: {}".format(msg))
                                        size = client_socket.sendall(msg) or None
                                        #logging.debug("WRITE SIZE: {}".format(size))
                                        return size

                                    xfer = xmodem.XMODEM(_getc, _putc)
                                    with open(filename, "wb") as fh:
                                        xfer.recv(fh)
                            else:
                                logging.warning("Invalid message received")
                                break
                        finally:
                            pass
                finally:
                    logging.info('Disconnected')
                    client_socket.close()
        finally:
            pass

    @property
    def thread(self):
        return self._thread


class DataReceiverConfigurationError(Exception):
    pass


class DataReceiverRuntimeError(Exception):
    pass

if __name__ == '__main__':
    a = argparse.ArgumentParser()
    a.add_argument("port", help="TCP port to listen on", type=int)
    a.add_argument("directory", help="Output directory")
    args = a.parse_args()

    dm = DataReceiver(args.port, args.directory)

    dm.thread.join()
    logging.info("Stopped listening for data...")
