#!/bin/sh
# Device tests 2 (Wi-Fi outage while awake) and 3 (Broadlink unplugged), simulated against
# a built package's FROZEN driver (arm64 under emulation), with a fake RM4 mini at
# 192.0.2.10 and a core-like WebSocket client (ws_tests23.py). No network at all.
#
# usage, from the repository root, with the package built there (see CLAUDE.md):
#   docker run --rm --user root --platform linux/arm64 --network none --cap-add NET_ADMIN \
#     -v "$PWD/tests/device_sim":/w:ro -v "$PWD":/p:ro \
#     unfoldedcircle/r2-pyinstaller:3.11.12-0.3.0 sh /w/run_in_container.sh [package file name]
# (Git Bash on Windows: prefix MSYS_NO_PATHCONV=1 and pass the paths through cygpath -w.)
# root + NET_ADMIN: the fake binds udp/80 and the Wi-Fi is a loopback alias set up and
# taken down with ioctl. Expected: "== 12/12 as expected" and "tracebacks: 0".
# Run 2026-11-27 on uc-intg-broadlink_driver-1.3.0-madalone.5-aarch64.tar.gz: 12/12.
set -u
PKG="${1:-uc-intg-broadlink_driver-1.3.0-madalone.5-aarch64.tar.gz}"
rm -rf /tmp/run && mkdir -p /tmp/run/app /tmp/run/cfg
cd /tmp/run/app && tar xzf "/p/$PKG" --no-same-owner || exit 9
chmod +x bin/driver
cat > /tmp/run/cfg/config.json <<'EOF'
[{"identifier": "a0b1c2d3e4f5", "name": "RM4 mini", "address": "192.0.2.10", "data": {}, "wake_wait_sec": 5.0}]
EOF
python3 -I -u /w/fake_rm4.py > /tmp/run/fake.txt 2>&1 &
sleep 2
python3 -I -c "
import fcntl, socket, struct
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sin = lambda a: struct.pack('16sH2s4s8s', b'lo:9', socket.AF_INET, bytes(2), socket.inet_aton(a), bytes(8))
fcntl.ioctl(s, 0x8916, sin('192.0.2.10')); fcntl.ioctl(s, 0x891C, sin('255.255.255.255'))
"
(UC_CONFIG_HOME=/tmp/run/cfg UC_DISABLE_MDNS_PUBLISH=true UC_INTEGRATION_INTERFACE=127.0.0.1 timeout 300 ./bin/driver > /tmp/run/log.txt 2>&1) &
i=0; while [ $i -lt 240 ]; do grep -q "Driver is up" /tmp/run/log.txt 2>/dev/null && break; sleep 0.5; i=$((i+1)); done
echo "## $(grep -o 'Driver is up.*' /tmp/run/log.txt | head -1) (after $i half-seconds)"
python3 -I -u /w/ws_tests23.py
sleep 2
pkill -f "bin/driver"; pkill -f fake_rm4.py
echo "## ---- driver log (warnings and BL-9 lines)"
grep -E "WARNING|ERROR|Traceback|Cannot|Gave up|trying once|reconnecting|Wait for the remote|authenticat" /tmp/run/log.txt | cut -c1-220
echo "## tracebacks: $(grep -c Traceback /tmp/run/log.txt)"
