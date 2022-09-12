import argparse
import binascii
import logging
import os
import re
import serial
import stat
import struct
import sys
import time as tm
import xmodem

from datetime import datetime

connection = None
lineend = "\r"
modem = True
ping = False
re_modem_resp = re.compile(b"""(OK
              |ERROR
              |BUSY
              |NO\ DIALTONE
              |NO\ CARRIER
              |RING
              |NO\ ANSWER
              |READY
              |GOFORIT
              |NAMERECV
              |CONNECT(?:\s+\d+)?)
              [\r\n]*$""", re.X)
re_signal = re.compile(r'^\+CSQ:(\d)', re.MULTILINE)

FILENAME = 0x1c
GOFORIT = 0x1d
STARTXFER = 0x1e
NAMERECV = 0x1f


def _signal_check(min_signal=3):
    # Check we have a good enough signal to work with (>3)
    signal_test = _send_receive_messages("AT+CSQ?", command=True)
    if signal_test == "":
        raise Exception(
            "No response received for signal quality check")
    signal_level = re_signal.search(signal_test)

    if signal_level:
        try:
            signal_level = int(signal_level.group(1))
            logging.debug("Got signal level {}".format(signal_level))
        except ValueError:
            raise Exception(
                "Could not interpret signal from response: {}".format(
                    signal_test))
    else:
        raise Exception(
            "Could not interpret signal from response: {}".format(signal_test))

    if type(signal_level) == int and signal_level >= min_signal:
        return True
    return False


def _start_data_call():
    if args.modem:
        _send_receive_messages("AT", command=True)
        _send_receive_messages("ATE0\n", command=True)
        _send_receive_messages("AT+SBDC", command=True)

        while not _signal_check():
            tm.sleep(3)

        response = _send_receive_messages("ATDT00881600005478", command=True)
        if not response.splitlines()[-1].startswith("CONNECT "):
            raise Exception(
                "Error opening call: {}".format(response))
    return True


# TODO: Too much sleeping, use state based logic
def _end_data_call():
    global connection

    if args.modem:
        logging.debug("Two second sleep")
        tm.sleep(2)
        logging.debug("Two second sleep complete")
        response = _send_receive_messages("+++".encode(), raw=True, command=True)
        logging.debug("One second sleep")
        tm.sleep(1)
        logging.debug("One second sleep complete")

        if response.splitlines()[-1] != "OK":
            raise Exception(
                "Did not switch to command mode to end call")

        response = _send_receive_messages("ATH0")

        if response.splitlines()[-1] != "OK":
            raise Exception("Did not hang up the call")
        else:
            logging.debug("Sleeping another second to wait for the line")
            tm.sleep(1)


def _process_file_message(filename):
    global connection

    def _callback(total_packets, success_count, error_count):
        logging.debug("{} packets, {} success, {} errors".format(total_packets,
                                                                 success_count,
                                                                 error_count))

    def _getc(size, timeout=1):
        read = connection.read(size=size) or None
        logging.debug("_getc read {} bytes from data line".format(
            len(read) if read else "no"
        ))
        return read

    def _putc(data, timeout=1):
        logging.debug("_putc wrote {} bytes to data line".format(
            len(data) if data else "no"
        ))
        size = connection.write(data=data)
        return size

    if _start_data_call():
        _send_filename(filename)

        xfer = xmodem.XMODEM(_getc, _putc)

        stream = open(filename, 'rb')
        xfer.send(stream, callback=_callback)
        logging.debug("Finished transfer")
        _end_data_call()

        return True
    return False


def _send_receive_messages(message, raw=False, command=False,
                           no_response=False):
    global connection, lineend, re_modem_resp

    if not connection.isOpen():
        raise Exception(
            'Cannot send message; data port is not open')

    if not raw:
        sendstr = "{}{}".format(message.strip(), lineend).encode("latin-1") \
            if command else None
        connection.write(sendstr)
        logging.info('Message sent: "{}"'.format(message.strip()))
    else:
        # FIXME: Assuming int messages are single length
        sendstr = message \
                  if type(message) != int \
                  else message.to_bytes(1, sys.byteorder)
        connection.write(sendstr)
        logging.debug(
            "Binary message of length {} bytes sent".format(len(sendstr)))

    if no_response:
        return

    reply = bytearray()
    modem_response = False

    while not modem_response:
        logging.debug("Waiting {}".format(connection.in_waiting))
        reply += connection.read(connection.in_waiting or 1)

        if not len(reply):
            logging.debug("Waiting for response...")
            tm.sleep(1)
            continue
        else:
            if command:
                cmd_match = re_modem_resp.search(reply.strip())
                if cmd_match:
                    tm.sleep(0.1)
                    if not connection.in_waiting:
                        modem_response = True
            else:
                # TODO: NO CARRIER DETECT
                modem_response = True

    if raw:
        logging.info("Response of {} bytes received".format(len(reply)))
    else:
        reply = reply.decode().strip()
        logging.info('Response received: "{}"'.format(reply))

    return reply


def _send_filename(filename):
    global ping

    if ping:
        logging.warning("PING MODE: we'll only be sending single bytes")
        while True:
            res = _send_receive_messages("@".encode(), raw=True)
            logging.info("RES: {}".format(res.decode()))
            tm.sleep(1)
    else:
        logging.info("Standard processing")

    # Assuming byte order remains the same between hosts
    logging.info("Sending init byte")
    res = _send_receive_messages(b"@", raw=True)

    while res[-1] != int.from_bytes(b"A", sys.byteorder):
        logging.info("Sending another init byte")
        res = _send_receive_messages(b"@", raw=True)

    logging.info("Received init byte response")
    res = _send_receive_messages(FILENAME, raw=True)

    if res != GOFORIT.to_bytes(1, sys.byteorder):
        raise Exception(
            "Required response for FILENAME command not received")

    # We can only have two byte lengths, and we don't escape the two
    # markers characters since we're using the length marker with
    # otherwise fixed fields. We just use 0x1b as validation of the
    # last byte of the message
    bfile = os.path.basename(filename).encode("latin-1")[:255]
    file_length = os.stat(filename)[stat.ST_SIZE]
    length = len(bfile)
    buffer = bytearray()
    buffer += struct.pack("BB", 0x1a, length)
    buffer += struct.pack("{}s".format(length), bfile)
    buffer += struct.pack("q", file_length)
    buffer += struct.pack("q", 1)
    buffer += struct.pack("q", 1)
    buffer += struct.pack("iB",
                          binascii.crc32(bfile) & 0xffff,
                          0x1b)

    res = _send_receive_messages(buffer, raw=True)
    if res[0] != NAMERECV:
        raise Exception(
            "Could not transfer filename first: {}".format(res))
    _send_receive_messages(STARTXFER, no_response=True, raw=True)


def main(port, files, virtual=False):
    global connection
    connection = serial.Serial(
        port=port,
        timeout=float(60),
        write_timeout=float(60),
        baudrate=9600,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        rtscts=virtual,
        dsrdtr=virtual
    )

    try:
        if connection.is_open:
            for file in files:
                logging.info("Processing {}".format(file))

                if not os.path.isfile(file):
                    logging.warning("{} is not a regular file, skipping".
                                    format(file))
                else:
                    _process_file_message(file)
        else:
            raise RuntimeError("Port isn't open")
    finally:
        connection.close()


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("-p", "--port", default="ttyDUFF")
    a.add_argument("-t", "--test", default=False, action="store_true")
    a.add_argument("-m", "--modem", dest="modem", action="store_false",
                   default=True)
    a.add_argument("files", nargs="+")
    args = a.parse_args()
    logging.basicConfig(level=logging.DEBUG)
    modem = args.modem
    ping = args.test
    main(args.port, args.files, virtual=not args.modem)
