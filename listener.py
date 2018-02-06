#!/usr/bin/env python3

import argparse
import logging
import os
import shlex
from subprocess import call, Popen, PIPE
import sys
import time

logging.basicConfig(level=logging.DEBUG)

def get_args():
    a = argparse.ArgumentParser()
    a.add_argument("port", help="Port to listen on", type=int)
    a.add_argument("command", help="Command file to run when port is listening")
    a.add_argument("--interval", "-i", help="Time to sleep between checks", default=10, type=int)
    a.add_argument("--wait-interval", "-w", help="Time to sleep between checks for port disappearing again", default=30, type=int)
    return vars(a.parse_args())

def check_for_port(port):
    match = False
    
    with Popen(["ss", "-plnt4"], 
        stdout=PIPE,
        universal_newlines=True) as proc:
        for line in proc.stdout:
            listener = str(line.split()[3])

            try:
               port = int(listener[listener.index(':')+1:])
            except (TypeError, ValueError, IndexError):
                continue
            
            if port == args["port"]:
                logging.debug("We have a matching port listening")
                match = True
                break
    return match
    
if __name__ == "__main__":
    args = get_args()
    
    logging.info("Listening for connection on port {}".format(args['port']))

    while True:
        match = check_for_port(args["port"])
                
        if match:
            try:
                logging.info("Running {} for activation on port {}".format(args["command"], args["port"]))
                rc = call(shlex.split(args["command"]))
                logging.info("Completed execution with rc {}, returning to listen state".format(rc))
            except Exception:
                logging.warning("Problem encountered: {}".format(sys.exc_info()[0]))
            
            while check_for_port(args["port"]):
                logging.debug("Waiting for {} until port disappears to resume checking".format(args["wait_interval"]))
                time.sleep(args["wait_interval"])
        else:
            logging.debug("Sleeping for {} seconds".format(args["interval"]))
            time.sleep(args["interval"])


                

                

