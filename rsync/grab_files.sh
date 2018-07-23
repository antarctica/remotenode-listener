#!/bin/bash

if [ $# -lt 2 ]; then
    echo "Usage: grab_files.sh <port> <source> <user> <destination>"
    exit 1
fi
    
PORT=$1
SOURCE=$2
USER="${3:-}"
DESTINATION="${4:-rssh}"

[ "$USER" != "" ] && USER="${USER}@"
DESTINATION=${DESTINATION%/}

echo "Syncing from ${USER}localhost:$PORT $SOURCE"
rsync -avh --partial --port=$1 ${USER}localhost:$SOURCE $DESTINATION/
exit $?
