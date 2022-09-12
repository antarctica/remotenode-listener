import logging
import serial
import threading
import xmodem


def main(filename="testout.txt"):

    recv = serial.Serial(
        port="tty2",
        timeout=float(60),
        write_timeout=float(60),
        baudrate=9600,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        rtscts=True,
        dsrdtr=True
    )

    def recv_getc(size, timeout=1):
        read = recv.read(size=size) or None
        logging.debug("RECV _getc read {} bytes from data line".format(
            len(read) if read else "no"
        ))
        return read

    def recv_putc(data, timeout=1):
        logging.debug("RECV _putc wrote {} bytes to data line".format(
            len(data) if data else "no"
        ))
        size = recv.write(data=data)
        return size

    recv_xfer = xmodem.XMODEM(recv_getc, recv_putc)

    with open(filename, "wb") as fh:
        recv_xfer.recv(fh)

    logging.debug("Finished transfer")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    main()