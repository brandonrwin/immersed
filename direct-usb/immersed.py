"""
Tried to follow these instructions: https://justinwlin.netlify.app/blog/immersed-low-latency/
But I can't get it to work on my Ventura Mac.
"""

import os
import sys
import time
import biplist
import subprocess
import json
import re
import threading

DATA_KEY = 'Data'
FORCE_IP_ADDRESS_KEY = "ForceIPAddress"
DEVICE_CONNECTED_REGEX = re.compile(r'Quest_Pro')
REVERSE_TEXT = 'UsbFfs tcp:21000 tcp:21000'
ADB_BIN = '/Users/brandon/platform-tools/adb'
IMMERSED_APP = '/Applications/Immersed.app'
IMMERSED_PLIST = os.path.expanduser('~/Library/Preferences/team.Immersed.plist')

# For finding IP addresses in ifconfig output.
ip_re = re.compile(r'inet 192.168.50.36/24 brd 192.168.50.255')
broadcast_re = re.compile(r'inet ((?:[0-9]{1,3}\.?){4})\/[1-9]{1,2} brd ((?:[0-9]{1,3}\.?){4})', re.M)
    
class Problem(Exception):
    pass


def adb(args, query=False, quiet=False):
    """ run the adb command on the device """
    return run([ADB_BIN] + args, query=query, quiet=quiet)


def run(args, query=False, quiet=False, error=True):
    """ run the command on the host """
    try:
        if query:
            return subprocess.check_output(args, stderr=subprocess.STDOUT).decode()
        else:
            subprocess.check_output(args, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        if not quiet:
            print("Error running: {cmd}:\n{error}".format(
                cmd=' '.join(args),
                error=e.output.decode()
            ))
        if error:
            raise



def is_device_connected():
    try:
        output = adb(['devices', '-l'], query=True, quiet=True)
    except subprocess.CalledProcessError as e:
        return False

    for line in output.strip().split('\n'):
        if DEVICE_CONNECTED_REGEX.findall(line):
            return True

    return False


def is_reverse_enabled():
    try:
        output = adb(['reverse', '--list'], query=True, quiet=True)
    except subprocess.CalledProcessError as e:
        output = ''
    print(output)
    reverse_running = REVERSE_TEXT in output
    return reverse_running


def set_adb_reverse(set_to_local):
    """ Set up the adb reverse. """
    if not is_device_connected():
        raise Problem("No device!")

    if set_to_local:
        # enable if not already
        if not is_reverse_enabled():
           adb(['reverse', 'tcp:21000', 'tcp:21000'])
        # verify that reverse is enabled
        if not is_reverse_enabled():
            raise Problem("Couldn't enable reverse: {}")
    else:
        if set_to_local:
            # disable it
            if not is_reverse_enabled:
                adb(['reverse', 'tcp:21000', '--remove'])
            if is_reverse_enabled():
                raise Problem("Couldn't disable reverse: {}")
        print("adb reverse enabled!")


def is_immersed_running(quest=False):
    """ See if Immersed is running, on desktop or quest. """
    running = True
    try:
        if quest:
            # exit code 1 if not found
            adb('shell pidof Immersed.quest'.split(), quiet=True)
            print("Immersed quest app is running!")
        else:
            # exit code 1 if not found
            run(['pgrep', '-f', '{}/Contents/MacOS/Immersed'.format(IMMERSED_APP)], quiet=True)
            print("Immersed desktop app is running!")
    except subprocess.CalledProcessError as e:
        # from non-zero exit code above.
        running = False

    return running


def start_immersed(quest=False, timeout=10):
    """ Start the desktop and VR client. """
    if quest:
        # start the quest app
        print("Starting the quest app:", end=''); sys.stdout.flush()
        output = adb(
            'shell cmd package resolve-activity --brief -c android.intent.category.LAUNCHER Immersed.quest'.split(),
            query=True
        )
        activity = output.strip().split('\n')[-1].strip()
        print(activity + "...", end=''); sys.stdout.flush()
        adb('shell am start -n'.split() + [activity])
    else:
        # start the desktop client
        print("Starting the desktop app...", end=''); sys.stdout.flush()
        run(['open', '-n', '/Applications/Immersed.app'])

    # make sure it actually started
    t = time.time() + timeout
    while time.time() < t:
        if is_immersed_running(quest):
            break
        time.sleep(1)
    else:
        where = 'quest' if quest else 'computer'
        raise Problem("Immersed didn't seem to start on the {}.".format(where))

    print("Done!")


def get_broadcast_ip():
    output = run('ifconfig'.split(), query=True)
    result = re.findall(r'broadcast\s((?:[0-9]{1,3}\.?){4})', output)
    if not result:
        raise Problem("No broadcast IP found")
    elif len(result) > 1:
        raise Problem("Multiple broadcast IPs: {}".format(result))

    return result[0]


def get_quest_ip():
    """ Get the quest IP and broadcast ip. """
    output = adb('shell ip addr show wlan0'.split(), query=True)

    result = broadcast_re.findall(output)
    if not result:
        raise Problem("No broadcast IP. Quest might not be on!: {}".format(output))
    elif len(result) > 1:
        raise Problem("Multiple broadcast IPs: {}".format(output))

    return result[0]


def kill_immersed(quest=False):
    """ Gently kill the desktop app or quest client. Wait until the process exits. """
    # Kill the desktop client, if needed
    if not is_immersed_running(quest):
        return

    # nicely kill Immersed.
    if quest:
        print("Killing the quest app...", end=''); sys.stdout.flush()
        adb('shell am force-stop Immersed.quest'.split())

    else:
        print("Killing the desktop app.", end=''); sys.stdout.flush()
        run(['pkill', '-f', 'Immersed.app/Contents/MacOS/Immersed'])

    # Allow immersed to exit cleanly
    timeout = time.time() + 10
    while time.time() < timeout:
        if not is_immersed_running(quest):
            break
        time.sleep(1)
    else:
        raise Problem("Tried to kill Immersed, but it didn't die!?")

    print("Done!")


def read_immersed_plist():
    # open it
    preferences = biplist.readPlist(IMMERSED_PLIST)
    data_text = preferences[DATA_KEY]
    # parse the data
    data = json.loads(data_text)
    return preferences, data


def write_immersed_plist(preferences, data):
    preferences[DATA_KEY] = json.dumps(data)
    biplist.writePlist(preferences, IMMERSED_PLIST, binary=True)
    return IMMERSED_PLIST

def edit_immersed_plist(set_to_local):
    """ Edit the plist. The immersed client can't be running. If it is, it's murdered dead. """

    if is_immersed_running():
        raise Problem("Can't edit the preferences. Immersed is running.")

    run(['/usr/bin/chflags', 'nouchg', IMMERSED_PLIST])

    if set_to_local:
        connection = 'wired'
        forced_ip_address = '127.0.0.1'
    else:
        connection = 'WiFi'
        forced_ip_address = ''

    preferences, data = read_immersed_plist()

    print("Editing preferences for {} connection: {!r}".format(connection, IMMERSED_PLIST))
    # modify and write the value
    data[FORCE_IP_ADDRESS_KEY] = forced_ip_address
    write_immersed_plist(preferences, data)
    print("Done!")

    # lock/unlock the file, so immersed can't change it? This seems bad, since useful preferences won't be saved. Is this needed?
    if set_to_local:
        # lock
        run(['/usr/bin/chflags', 'uchg', IMMERSED_PLIST])


def set_packet_filter(set_to_local):
    """ NOTE: Remove this with next Immersed app update.
    Edit /etc/pf.conf to block UDP from the quest's IP address.

    Disables/enables packet filtering. Add/removes a dynamic anchor, so no file writing is reqiured, and we can't mess
    up other rules!
    """
    # delete (flush) our immersed block rules.
    run('pfctl -a immersedblock -F rules'.split())

    if not set_to_local:
        # disable packet filtering. Assuming that it was disabled to begin with!
        run('pfctl -d'.split(), quiet=True, error=False)
        return
    else:
        run('pfctl -e'.split(), quiet=True, error=False)

    broadcast_ip = get_broadcast_ip()
    block_quest_udp_rule = 'block in proto udp from any to {}\n'.format(broadcast_ip)
    # block_quest_udp_rule = "# "block drop in proto udp from any to {}".format(broadcast_ip)
    print("UDP block rule:", block_quest_udp_rule)
    # See "To add rules to an anchor using pfctl" at https://www.openbsd.org/faq/pf/anchors.html
    pfctl = subprocess.Popen('pfctl -a immersedblock -f -'.split(), stdin=subprocess.PIPE)
    #pfctl = subprocess.Popen('cat'.split(), stdin=subprocess.PIPE)
    # feed it the rule
    stdout, stderr = pfctl.communicate(input=block_quest_udp_rule.encode())
    if pfctl.returncode != 0:
        print("pfctl error:")
        print(stdout)
        print(stderr)
        raise Problem("Couldn't set immersed block anchor: {}".format(stderr))




def setup_connection(usb, kill):
    """  Setup the connection for usb, or not.
    usb: If true,
    kill_desktop_client:
    """
    # Kill the desktop client
    if kill:
        kill_immersed(quest=False)
        kill_immersed(quest=True)

    # edit the plist
    edit_immersed_plist(set_to_local=usb)

    # Set up the reverse forwarding stuffs
    set_adb_reverse(set_to_local=usb)

    set_packet_filter(set_to_local=usb)

    start_immersed(quest=False)
    start_immersed(quest=True)

    # verify that the force address is still correct
    if usb:
        preferences, data = read_immersed_plist()
        if data[FORCE_IP_ADDRESS_KEY] != '127.0.0.1':
            raise Problem("Forced up is not set, but we set it and it should be!?")


def monitor():
    pass


def start_monitor():
    threading.Timer(10, )
    pass


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser("Setup Immersed to use USB connection, in macOS.")
    parser.add_argument('--monitor', '-m', help="Monitor if a quest is plugged in. Checks every 5 seconds. Runs --wired when plugged in, and --wifi when unplugged.")
    parser.add_argument("--usb", '-u', help='Set up for wired USB connection. Will kill the Immersed desktop client, unless --nokill is set.', action='store_true')
    parser.add_argument("--wifi", '-w', help='Set up for WiFi connection (undo everything done with --usb. Will kill the Immersed desktop client, unless --nokill is  set.', action='store_true')
    parser.add_argument('--restart', '-r', help="Kill the desktop and quest apps. Supposedly Immersed writes preferences on exit, and caches old things, so we can't start until they're deaded.", action='store_true')
    options = parser.parse_args()

    if not (options.usb or options.wifi) and options.restart:
        print("Restarting Immersed")
        kill_immersed()
        kill_immersed(quest=True)
        start_immersed()
        start_immersed(quest=True)
        sys.exit()

    if not (options.usb ^ options.wifi):
        parser.error("--usb or --wifi")

    setup_connection(options.usb, options.restart)
