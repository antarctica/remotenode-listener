import argparse
import binascii

import logging
import os
import re
import serial
import stat
import struct
import time as tm
import xmodem

from datetime import datetime

connection = None



def _send_receive_messages(message, raw=False, dont_decode=False,
                           timeout_override=None):
    """
    send message through data port and recieve reply. If no reply, will timeout according to the
    data_timeout config setting

    python 3 requires the messages to be in binary format - so encode them, and also decode response.
    'latin-1' encoding is used to allow for sending file blocks which have bytes in range 0-255,
    whereas the standard or 'ascii' encoding only allows bytes in range 0-127

    readline() is used for most messages as it will block only until the full reply (a signle line) has been
    returned, or if no reply recieved, until the timeout. However, file_transfer_messages (downloading file
    blocks) may contain numerous newlines, and hence read() must be used (with an excessive upper limit; the
    maximum message size is ~2000 bytes), returning at the end of the configured timeout - make sure it is long enough!
    """
    global connection

    if not connection.isOpen():
        raise Exception(
            'Cannot send message; data port is not open')
    connection.flushInput()
    connection.flushOutput()

    if not raw:
        connection.write(
            "{}{}".format(message.strip(), "\n").encode("latin-1"))
        logging.info('Message sent: "{}"'.format(message.strip()))
    else:
        connection.write(message)
        logging.debug(
            "Binary message of length {} bytes sent".format(len(message)))

    # It seems possible that we don't get a response back sometimes, not sure why. Facilitate breaking comms
    # for another attempt in this case, else we'll end up in an infinite loop
    bytes_read = 0

    reply = bytearray()
    modem_response = False
    start = datetime.utcnow()

    msg_timeout = 30
    if timeout_override:
        msg_timeout = timeout_override

    while not modem_response:
        tm.sleep(0.1)
        reply += connection.read_all()
        bytes_read += len(reply)

        duration = (datetime.utcnow() - start).total_seconds()
        if not len(reply):
            if duration > msg_timeout:
                logging.warning(
                    "We've read 0 bytes continuously for {} seconds, abandoning reads...".format(
                        duration
                    ))
                # It's up to the caller to handle this scenario, just give back what's available...
                raise Exception(
                    "Response timeout from serial line...")
            else:
                # logging.debug("Waiting for response...")
                tm.sleep(30)
                continue

        start = datetime.utcnow()
        if not dont_decode:
            logging.debug("Reply received: '{}'".format(reply.decode().strip()))
            modem_response = True

        cmd_match = re.compile(b"""(OK
                                |ERROR
                                |BUSY
                                |NO\ DIALTONE
                                |NO\ CARRIER
                                |RING
                                |NO\ ANSWER
                                |READY
                                |GOFORIT
                                |NAMERECV
                                |CONNECT(?: \d{3,5})?)
                                [\r\n]*$""", re.X).search(reply.strip())
        if cmd_match:
            tm.sleep(0.1)
            if not connection.in_waiting:
                modem_response = True

    if dont_decode:
        logging.info("Response of {} bytes received".format(bytes_read))
    else:
        reply = reply.decode().strip()
        logging.info('Response received: "{}"'.format(reply))

    return reply


def _process_file_message(file):
    _send_filename(file)


def _send_filename(filename):
    global connection

    while True:
        res = _send_receive_messages("@")

        logging.info("Response: {}".format(res))


def main(port, files):
    global connection
    connection = serial.Serial(
        port=port,
        timeout=float(30),
        write_timeout=float(30),
        baudrate=115200,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        rtscts=True,
        dsrdtr=True
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
    a.add_argument("files", nargs="+")
    args = a.parse_args()
    logging.basicConfig(level=logging.DEBUG)
    main(args.port, args.files)
