import logging
import serial
import sys
import xmodem


def main(filename="/home/jambyr/scratch/csw15.txt"):
    send = serial.Serial(
        port="tty1",
        timeout=float(60),
        write_timeout=float(60),
        baudrate=9600,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        rtscts=True,
        dsrdtr=True
    )

    def send_callback(total_packets, success_count, error_count):
        logging.debug("{} packets, {} success, {} errors".format(total_packets,
                                                                 success_count,
                                                                 error_count))

    def send_getc(size, timeout=1):
        read = send.read(size=size) or None
        logging.debug("SEND _getc read {} bytes from data line".format(
            len(read) if read else "no"
        ))
        return read

    def send_putc(data, timeout=1):
        logging.debug("SEND _putc wrote {} bytes to data line".format(
            len(data) if data else "no"
        ))
        size = send.write(data=data)
        return size

    send_xfer = xmodem.XMODEM(send_getc, send_putc)
    send_stream = open(filename, 'rb')

    send_xfer.send(send_stream, callback=send_callback)
    send_stream.close()
    logging.debug("Finished transfer")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    main(sys.argv[1])