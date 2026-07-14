#Grott Growatt monitor :  Proxy
#
# Updated: 2022-08-07
# Version 2.7.5

import socket
import select
import time
from concurrent.futures import ThreadPoolExecutor

from grottdata import procdata, decrypt, format_multi_line
from grottprotocol import FrameBuffer, FrameError, SUPPORTED_PROTOCOLS

#import mqtt
import paho.mqtt.publish as publish

#import libscrc for additional crc checking
# for compat reason (generate a message in the log) also done in proxy _init_
try:
    import libscrc
except ImportError:
    libscrc = None
    print("\t **********************************************************************************")
    print("\t - Grott - libscrc not installed, no CRC checking only record validation on length!")
    print("\t **********************************************************************************")


# Changing the buffer_size and delay, you can improve the speed and bandwidth.
# But when buffer get to high or delay go too down, you can broke things
buffer_size = 4096
#buffer_size = 65535
delay = 0.0002
SOCKET_IO_TIMEOUT = 30.0
INCOMPLETE_FRAME_TIMEOUT = 30.0
SELECT_POLL_TIMEOUT = 1.0
MAX_PENDING_CONNECTIONS = 4


def send_all(sock, data):
    """Write all bytes, using socket.sendall when the socket provides it."""
    native_sendall = getattr(sock, "sendall", None)
    if native_sendall is not None:
        native_sendall(data)
        return

    remaining = memoryview(data)
    while remaining:
        sent = sock.send(remaining)
        # A few legacy test/socket adapters return None to mean the whole
        # buffer was accepted. Real sockets return a positive byte count.
        if sent is None:
            return
        if sent <= 0:
            raise BrokenPipeError("socket write made no progress")
        remaining = remaining[sent:]


def validate_record(xdata):
    """Validate one complete record without allowing malformed input to escape."""
    try:
        data = bytes.fromhex(xdata) if isinstance(xdata, str) else bytes(xdata)
    except (TypeError, ValueError):
        return 8

    if len(data) < 8:
        return 8

    protocol = data[3]
    if protocol not in SUPPORTED_PROTOCOLS:
        return 8
    declared_length = int.from_bytes(data[4:6], "big")
    crc_length = 2 if protocol in (5, 6) else 0
    if declared_length < 2 or len(data) != 6 + declared_length + crc_length:
        return 8

    if crc_length and libscrc is not None:
        received_crc = int.from_bytes(data[-2:], "big")
        if received_crc != libscrc.modbus(data[:-2]):
            return 8

    return 0


class Forward:
    def __init__(self):
        self.forward = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def start(self, host, port):
        try:
            self.forward.settimeout(SOCKET_IO_TIMEOUT)
            self.forward.connect((host, port))
            return self.forward
        except OSError as e:
            print(
                "\t - Grott - grottproxy forward error "
                f"({type(e).__name__})"
            )
            self.forward.close()
            return False

class Proxy:
    def __init__(self, conf):
        print("\nGrott proxy mode started")

        # for compatibility reasons test if libscrc is installed and send error message
        # if not installed processing wil continue but records will only be validated on length and not on crc.
        if libscrc is None:
            print("\t **********************************************************************************")
            print("\t - Grott - libscrc not installed, no CRC checking only record validation on length!")
            print("\t **********************************************************************************")

        self.input_list = []
        self.channel = {}
        self.framers = {}
        self.frame_started_at = {}
        self._init_connector()
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        #set default grottip address
        if conf.grottip == "default" : conf.grottip = '0.0.0.0'
        self.server.bind((conf.grottip, conf.grottport))
        #socket.gethostbyname(socket.gethostname())
        try:
            hostname = (socket.gethostname())
            print("Hostname :", hostname)
            print("IP : ", socket.gethostbyname(hostname), ", port : ", conf.grottport, "\n")
        except:
            print("IP and port information not available")

        self.server.listen(200)
        self.forward_to = (conf.growattip, conf.growattport)

    def main(self,conf):
        if self.server not in self.input_list:
            self.input_list.append(self.server)
        try:
            while 1:
                self._drain_pending_connections(conf)
                time.sleep(delay)
                ss = select.select
                inputready, _outputready, exceptready = ss(
                    self.input_list, [], self.input_list, SELECT_POLL_TIMEOUT
                )
                self._drain_pending_connections(conf)
                self._expire_incomplete_frames(conf)
                for failed_socket in exceptready:
                    if failed_socket is not self.server:
                        self._close_socket(failed_socket, conf)
                for readable_socket in inputready:
                    if readable_socket == self.server:
                        self.on_accept(conf)
                        continue
                    self._read_socket(readable_socket, conf)
        finally:
            self._shutdown_connector()

    def _init_connector(self):
        self._connector_shutdown = False
        self._pending_connections = {}
        self._connector = ThreadPoolExecutor(
            max_workers=MAX_PENDING_CONNECTIONS,
            thread_name_prefix="grott-upstream",
        )

    @staticmethod
    def _connect_upstream(host, port):
        return Forward().start(host, port)

    @staticmethod
    def _close_unpaired_socket(sock):
        if not sock:
            return
        try:
            sock.close()
        except OSError:
            pass

    def _close_shutdown_upstream(self, future):
        """Own cleanup of one non-cancelled connector result at shutdown."""
        if future.cancelled():
            return
        try:
            forward = future.result()
        except Exception:
            return
        self._close_unpaired_socket(forward)

    def _register_pair(self, clientsock, clientaddr, forward, conf):
        if conf.verbose:
            print("\t -", clientaddr, "has connected")
        self.input_list.extend((clientsock, forward))
        self.channel[clientsock] = forward
        self.channel[forward] = clientsock
        self.framers[clientsock] = FrameBuffer()
        self.framers[forward] = FrameBuffer()

    def _drain_pending_connections(self, conf):
        if not hasattr(self, "_pending_connections"):
            return
        completed = [
            future
            for future in self._pending_connections
            if future.done()
        ]
        for future in completed:
            clientsock, clientaddr = self._pending_connections.pop(future)
            try:
                forward = future.result()
            except Exception as error:
                print(
                    "\t - Grott - upstream connection failed "
                    f"({type(error).__name__})"
                )
                forward = False

            if self._connector_shutdown:
                self._close_unpaired_socket(clientsock)
                self._close_unpaired_socket(forward)
            elif forward:
                self._register_pair(
                    clientsock, clientaddr, forward, conf
                )
            else:
                if conf.verbose:
                    print("\t - Can't establish connection with remote server.")
                    print(
                        "\t - Closing connection with client side",
                        clientaddr,
                    )
                self._close_unpaired_socket(clientsock)

    def _shutdown_connector(self):
        if getattr(self, "_connector_shutdown", True):
            return
        self._connector_shutdown = True
        pending = list(self._pending_connections.items())
        self._pending_connections.clear()
        for future, (clientsock, _clientaddr) in pending:
            self._close_unpaired_socket(clientsock)
            if future.cancel():
                continue
            future.add_done_callback(self._close_shutdown_upstream)
        self._connector.shutdown(wait=False, cancel_futures=True)

    def _read_socket(self, source, conf):
        """Read one available chunk and process only its complete frames."""
        try:
            chunk = source.recv(buffer_size)
            if not chunk:
                self._close_socket(source, conf)
                return False

            framer = self.framers.setdefault(source, FrameBuffer())
            had_pending_frame = bool(framer.pending)
            frames = framer.feed(chunk)
            if framer.pending:
                if frames or not had_pending_frame:
                    self.frame_started_at[source] = time.monotonic()
                elif source not in self.frame_started_at:
                    self.frame_started_at[source] = time.monotonic()
            else:
                self.frame_started_at.pop(source, None)
            for frame in frames:
                self.s = source
                self.data = frame
                self.on_recv(conf)
            return True
        except (FrameError, OSError) as error:
            if conf.verbose:
                print(
                    "\t - Grott connection closed after transport error "
                    f"({type(error).__name__})"
                )
            self._close_socket(source, conf)
            return False
        except Exception as error:
            # A malformed record must be isolated to its connection. Keeping
            # the main select loop alive also preserves other logger sessions.
            print(
                "\t - Grott connection closed after malformed record "
                f"({type(error).__name__})"
            )
            self._close_socket(source, conf)
            return False

    def _expire_incomplete_frames(self, conf, now=None):
        now = time.monotonic() if now is None else now
        expired = [
            source
            for source, started_at in self.frame_started_at.items()
            if now - started_at > INCOMPLETE_FRAME_TIMEOUT
        ]
        for source in expired:
            if conf.verbose:
                print("\t - Grott incomplete frame timed out")
            self._close_socket(source, conf)

    def on_accept(self,conf):
        if not hasattr(self, "_pending_connections"):
            self._init_connector()
        self._drain_pending_connections(conf)
        try:
            clientsock, clientaddr = self.server.accept()
        except OSError as error:
            if conf.verbose:
                print(
                    "\t - Grott - client accept error "
                    f"({type(error).__name__})"
                )
            return

        if (
            self._connector_shutdown
            or len(self._pending_connections) >= MAX_PENDING_CONNECTIONS
        ):
            if conf.verbose:
                print("\t - Grott - upstream connection queue is full")
            self._close_unpaired_socket(clientsock)
            return

        try:
            clientsock.settimeout(SOCKET_IO_TIMEOUT)
        except OSError as error:
            if conf.verbose:
                print(
                    "\t - Grott - client timeout setup error "
                    f"({type(error).__name__})"
                )
            try:
                clientsock.close()
            except OSError:
                pass
            return

        try:
            future = self._connector.submit(
                self._connect_upstream,
                self.forward_to[0],
                self.forward_to[1],
            )
        except RuntimeError as error:
            print(
                "\t - Grott - upstream connection could not be scheduled "
                f"({type(error).__name__})"
            )
            self._close_unpaired_socket(clientsock)
            return
        self._pending_connections[future] = (clientsock, clientaddr)

    def on_close(self,conf):
        self._close_socket(getattr(self, "s", None), conf)

    def _close_socket(self, source, conf):
        if source is None:
            return
        if conf.verbose:
            #try / except to resolve errno 107: Transport endpoint is not connected
            try:
                print("\t -", source.getpeername(), "has disconnected")
            except OSError:
                print("\t -", "peer has disconnected")

        target = self.channel.pop(source, None)
        if target is not None:
            self.channel.pop(target, None)

        for sock in (source, target):
            if sock is None:
                continue
            if sock in self.input_list:
                self.input_list.remove(sock)
            self.framers.pop(sock, None)
            self.frame_started_at.pop(sock, None)
            try:
                sock.close()
            except OSError:
                pass

    def on_recv(self,conf):
        data = self.data
        print("")
        print("\t - " + "Growatt packet received:")
        print("\t\t ", self.channel[self.s])

        # Validation controls local telemetry decoding, not transparent proxy
        # forwarding. The command-block policy below remains authoritative.
        valid_record = validate_record(data) == 0
        if not valid_record:
            print(
                "\t - Grott - grottproxy - Invalid data record received; "
                "forwarding raw record without local processing"
            )

        # FILTER!!!!!!!! Detect if configure data is sent!
        header = "".join("{:02x}".format(n) for n in data[0:8])
        recordtype = header[12:16]
        protocol = header[6:8]
        if conf.blockcmd :
            #standard everything is blocked!
            print("\t - " + "Growatt command block checking started")
            blockflag = True
            blockreason = "record type not in whitelist"
            #partly block configure Shine commands
            if header[14:16] == "18" :
                blockreason = "configure command blocked"
                if conf.blockcmd :
                    if protocol == "05" or protocol == "06" : confdata = decrypt(data)
                    else :  confdata = data

                    #get conf command (location depends on record type), maybe later more flexibility is needed
                    if protocol == "06" : confcmd = confdata[76:80]
                    else: confcmd = confdata[36:40]

                    if header[14:16] == "18" :
                        #do not block if configure time command of configure IP (if noipf flag set)
                        if conf.verbose : print("\t - Grott: Shine Configure command detected")
                        if valid_record and (
                            confcmd == "001f"
                            or (confcmd == "0011" and conf.noipf)
                        ):
                            blockflag = False
                            if confcmd == "001f": confcmd = "Time"
                            if confcmd == "0011": confcmd = "Change IP"
                            if conf.verbose : print("\t - Grott: Configure command not blocked : ", confcmd)
                    else :
                        #All configure inverter commands will be blocked
                        if conf.verbose : print("\t - Grott: Inverter Configure command detected")

            #allow records:
            if recordtype in conf.recwl : blockflag = False

            if blockflag :
                print("\t - Grott: Record blocked: ", recordtype)
                print(
                    f"\t - Grott: Record {recordtype} blocked before forward to Growatt "
                    f"and before local publish (reason: {blockreason})"
                )
                if protocol == "05" or protocol == "06" : blockeddata = decrypt(data)
                else :  blockeddata = data
                print(format_multi_line("\t\t ",blockeddata))
                return

        # send data to destination
        send_all(self.channel[self.s], data)
        if not valid_record:
            return

        if len(data) > conf.minrecl :
            #process received data
            try:
                procdata(conf,data)
            except Exception as error:
                print(
                    "\t - Grott - local telemetry processing failed after raw "
                    f"forwarding ({type(error).__name__})"
                )
        else:
            if conf.verbose:
                print(
                    "\t - "
                    + f"Grott record {recordtype} forwarded to Growatt but not processed locally: "
                    + f"len={len(data)} minrecl={conf.minrecl}"
                )
                if getattr(conf, "diagnostic_logging", False):
                    print("\t - Short packet raw data:")
                    print(format_multi_line("\t\t ", data.hex()))
                    if protocol == "05" or protocol == "06":
                        try:
                            print("\t - Short packet decrypted data:")
                            print(format_multi_line("\t\t ", decrypt(data)))
                        except Exception as error:
                            print(
                                "\t - Short packet decryption failed "
                                f"({type(error).__name__})"
                            )
