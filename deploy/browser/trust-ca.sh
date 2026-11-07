#!/bin/sh
set -eu
umask 077
mkdir -p /home/browser/.pki/nssdb
cat > /home/browser/ca.crt
grep -q 'BEGIN CERTIFICATE' /home/browser/ca.crt
certutil -N -d sql:/home/browser/.pki/nssdb --empty-password
certutil -A -d sql:/home/browser/.pki/nssdb -n TapperTaskCA -t 'C,,' -i /home/browser/ca.crt
