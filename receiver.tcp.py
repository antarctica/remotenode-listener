import argparse
import binascii
import logging
import os
import socket
import struct
import sys
import time as tm
import xmodem

from threading import Thread

FILENAME = 0x1c
GOFORIT = 0x1d
STARTXFER = 0x1e
NAMERECV = 0x1f


# Based on https://github.com/pyserial/pyserial/
# blob/master/examples/tcp_serial_redirect.py
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
                    client_socket.setsockopt(socket.IPPROTO_TCP,
                                             socket.TCP_NODELAY, 1)

                    client_socket.setsockopt(socket.SOL_SOCKET,
                                             socket.SO_RCVTIMEO,
                                             (1).to_bytes(8, sys.byteorder) +
                                             (0).to_bytes(8, sys.byteorder))
                    x = client_socket.getsockopt(socket.SOL_SOCKET,
                                                 socket.SO_RCVTIMEO,
                                                 16)
                    logging.info("Timeout seconds: {}, usecs: {}".format(
                        int.from_bytes(x[:8], sys.byteorder),
                        int.from_bytes(x[8:], sys.byteorder)))

                except AttributeError:
                    pass

                data = bytearray()
                lead_in = False
                preamble = False

                try:
                    while True:
                        try:
                            recv = client_socket.recv(4096)
                        except socket.error as e:
                            if e.errno == 11:
                                continue
                            else:
                                raise

                        if not recv:
                            continue
                        else:
                            data += recv

                        logging.info("Buffer size received: {}".
                                     format(len(data)))

                        if not lead_in and not preamble \
                                and data[-1] == int.from_bytes(b"@",
                                                               sys.byteorder):
                            logging.info("Got init byte, sending response")
                            client_socket.send(b"A")
                            data = bytearray()
                            continue

                        if not lead_in:
                            if data == FILENAME.to_bytes(1, sys.byteorder):
                                logging.debug("Sending FILENAME response...")
                                client_socket.send(GOFORIT.to_bytes(1, sys.byteorder))
                                data = bytearray()
                                lead_in = True
                            else:
                                logging.debug("No valid message received, "
                                              "start again...")
                            continue

                        if not preamble:
                            logging.info("Waiting for filename information...")
                            logging.debug("File message: {}".format(data))

                            try:
                                (lead, length) = struct.unpack_from("BB", data)

                                req_length = \
                                    struct.calcsize("=BB{}sqqqiB".format(
                                        length))

                                if len(data) != req_length:
                                    logging.warning("{} is not equal to "
                                                    "expected {} "
                                                    "bytes".format(len(data),
                                                                   req_length))
                                    # TODO: limit retries?
                                    continue

                                (filename, file_length) = struct.unpack_from(
                                    "{}sq".format(length),
                                    data,
                                    struct.calcsize("=BB"))
                                (chunk, total_chunks) = struct.unpack_from(
                                    "qq".format(length),
                                    data,
                                    struct.calcsize("=BB{}sq".format(length)))
                                (crc32, tail) = struct.\
                                    unpack_from("iB", data,
                                                struct.calcsize("=BB{}sqqq".
                                                                format(length)))
                            except struct.error:
                                continue

                            logging.info("Received filename infromation, "
                                         "checking...")
                            logging.debug("Filename length: {}".format(length))
                            logging.debug("File length: {}".format(file_length))
                            logging.debug("Filename: {}".format(filename))
                            logging.debug("Filename CRC: {}".format(crc32))

                            if lead == 0x1a and tail == 0x1b \
                               and binascii.crc32(filename) & 0xffff == crc32:
                                client_socket.send(NAMERECV.to_bytes(1, sys.byteorder))

                                with open("dataout.bin", "wb") as dataout:
                                    def _getc(size, timeout=1):
                                        try:
                                            read = client_socket.recv(size)
                                            dataout.write(read)
                                        except socket.error as e:
                                            if e.errno == 11:
                                                read = None

                                        logging.debug("READ {} DATA: {}".format(
                                            size,
                                            str(int.from_bytes(read,
                                                               sys.byteorder))
                                            if read else "none"))
                                        return read or None

                                    def _putc(msg, timeout=1):
                                        logging.debug("WRITE DATA: {}".format(msg))
                                        size = client_socket.send(msg)
                                        return size

                                    data = bytearray()

                                    while not len(data) or \
                                            data[-1] != STARTXFER:
                                        try:
                                            data += client_socket.recv(4096)
                                        except socket.error as e:
                                            if e.errno != 11:
                                                raise

                                    logging.warning("TEMP sleep for 5, "
                                                    "sender should not start")
                                    tm.sleep(5)

                                    client_socket.setsockopt(socket.SOL_SOCKET,
                                                             socket.SO_RCVTIMEO,
                                                             (10).to_bytes(8,
                                                                          sys.byteorder) +
                                                             (0).to_bytes(8,
                                                                          sys.byteorder))
                                    x = client_socket.getsockopt(
                                        socket.SOL_SOCKET,
                                        socket.SO_RCVTIMEO,
                                        16)
                                    logging.info(
                                        "Timeout seconds: {}, usecs: {}".format(
                                            int.from_bytes(x[:8],
                                                           sys.byteorder),
                                            int.from_bytes(x[8:],
                                                           sys.byteorder)))

                                    xfer = xmodem.XMODEM(_getc, _putc)
                                    with open(filename, "wb") as fh:
                                        xfer.recv(fh)
                            else:
                                logging.warning("Invalid message received")
                                break

                        logging.info("Resetting flags and data buffer")
                        data = bytearray()

                        lead_in = False
                        preamble = False


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
    logging.basicConfig(level=logging.DEBUG)
    logging.info("PyRMDataReceiver")

    a = argparse.ArgumentParser()
    a.add_argument("port", help="TCP port to listen on", type=int)
    a.add_argument("directory", help="Output directory")
    args = a.parse_args()

    dm = DataReceiver(args.port, args.directory)

    dm.thread.join()
    logging.info("Stopped listening for data...")
