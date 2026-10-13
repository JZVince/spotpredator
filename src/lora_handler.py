"""LoRa Handler for RYLR998 Module (UART AT Commands)"""
import logging
import time
try:
    import serial
except ImportError:
    serial = None

logger = logging.getLogger(__name__)


class LoRaHandler:
    """Simple interface for RYLR998 LoRa module via UART AT commands"""

    def __init__(self, port="/dev/serial0", baud_rate=115200, network_id=18, frequency=915):
        """
        Initialize LoRa module

        Args:
            port: Serial port (default /dev/serial0 for Pi)
            baud_rate: Baud rate (default 115200)
            network_id: Network ID (0-255, default 18)
            frequency: Frequency in MHz (default 915)
        """
        self.port = port
        self.baud_rate = baud_rate
        self.network_id = network_id
        self.frequency = frequency
        self.serial = None
        self.lora_available = False
        self._rx_buf = ""      # partial line not yet terminated by \r\n
        self._pending = []     # parsed +RCV messages not yet handed to poll_messages()

        if serial is None:
            logger.warning("pyserial not available, LoRa disabled")
            return

        try:
            # Open serial port
            self.serial = serial.Serial(
                port=port,
                baudrate=baud_rate,
                timeout=2
            )

            time.sleep(3)  # Wait for module to be ready on cold boot

            # Test communication. The old code did a SINGLE "AT" check here — but on cold boot
            # that one shot occasionally missed even though the module was healthy (verified by
            # manual AT test), leaving lora_available=False and silently disabling sends for the
            # whole run. Retry a few times so a slightly-slow/flaky module still gets detected.
            max_attempts = 5
            response = None
            for attempt in range(1, max_attempts + 1):
                response = self._send_command("AT")
                if response and "+OK" in response:
                    if attempt > 1:
                        logger.info(f"LoRa responded on attempt {attempt}/{max_attempts}")
                    break
                logger.debug(f"LoRa AT check {attempt}/{max_attempts} got: {response!r}")
                if attempt < max_attempts:
                    time.sleep(1)  # give the module another second, then retry

            if response and "+OK" in response:
                self.lora_available = True

                # Configure network ID
                self._send_command(f"AT+NETWORKID={network_id}")

                # Configure frequency
                self._send_command(f"AT+BAND={frequency}")

                logger.info(f"LoRa initialized: Network ID={network_id}, Frequency={frequency}MHz")
            else:
                # Make the consequence EXPLICIT in the log — a failed run used to look silent,
                # so we could never tell afterward whether messages were actually being sent.
                logger.warning(
                    f"LoRa module not responding after {max_attempts} attempts — "
                    f"LoRa DISABLED for this run, NO messages (heartbeats/alerts) will be sent."
                )

        except Exception as e:
            logger.error(f"Failed to initialize LoRa: {e}")
            self.lora_available = False

    def _send_command(self, command, expect_response=True):
        """
        Send AT command to LoRa module

        Args:
            command: AT command string
            expect_response: Whether to wait for response

        Returns:
            str: Response from module, or None if error
        """
        if not self.serial:
            return None

        try:
            # Keep any received messages before clearing the buffer — the old code threw them away,
            # so a station message that arrived just before one of our sends was silently lost.
            self._drain()
            self.serial.reset_input_buffer()

            # Send command
            self.serial.write(f"{command}\r\n".encode())
            time.sleep(0.3)

            if expect_response:
                # Read response (a message received in that window is kept, not mixed into it)
                response = self.serial.read(self.serial.in_waiting or 64).decode(errors="replace")
                response = "\n".join(l for l in self._split_rcv(response) if l).strip()
                logger.debug(f"Command: {command} | Response: {response}")
                return response

            return "OK"

        except Exception as e:
            logger.error(f"Failed to send command '{command}': {e}")
            return None

    def send_message(self, message, address=0):
        """
        Send message via LoRa

        Args:
            message: Message string to send
            address: Destination address (0 for broadcast)

        Returns:
            bool: True if sent successfully
        """
        if not self.lora_available:
            logger.warning("LoRa not available, cannot send message")
            return False

        try:
            # Format: AT+SEND=<address>,<length>,<message>
            message_length = len(message)
            command = f"AT+SEND={address},{message_length},{message}"

            response = self._send_command(command)

            if response and ("+OK" in response or "OK" in response):
                logger.info(f"Sent LoRa message: {message}")
                return True
            else:
                logger.warning(f"Failed to send LoRa message: {response}")
                return False

        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False

    def send_alert(self, predator_type, confidence, timestamp, date=None, position='C'):
        """
        Send predator alert

        Args:
            predator_type: Type of predator (e.g., 'dog', 'cat', 'bird')
            confidence: Detection confidence (0-1)
            timestamp: Detection time string (HH:MM:SS)
            date: Detection date string (MM/DD/YYYY)
            position: Turret position when detected (C, L1, L2, R1, R2)

        Returns:
            bool: True if sent successfully
        """
        # Message format: PREDATOR,<type>,<time>,<position>,<confidence>,<date>
        message = f"PREDATOR,{predator_type},{timestamp},{position},{confidence:.2f},{date or ''}"
        return self.send_message(message)

    @staticmethod
    def _parse_rcv(line):
        """+RCV=<address>,<length>,<data>,<RSSI>,<SNR> -> dict. <data> may itself contain commas,
        so it is cut by <length> rather than by splitting."""
        try:
            addr, length, rest = line[5:].split(",", 2)
            length = int(length)
            data = rest[:length]
            tail = rest[length + 1:].split(",")
            return {"address": int(addr), "length": length, "message": data,
                    "rssi": int(tail[0]) if tail and tail[0].lstrip("-").isdigit() else None,
                    "snr": int(tail[1]) if len(tail) > 1 and tail[1].lstrip("-").isdigit() else None}
        except (ValueError, IndexError):
            logger.warning(f"Unparseable LoRa line: {line!r}")
            return None

    def _split_rcv(self, text):
        """Pull complete +RCV lines out of text into the pending queue; return the other lines."""
        self._rx_buf += text
        *lines, self._rx_buf = self._rx_buf.replace("\r", "").split("\n")
        others = []
        for line in lines:
            if line.startswith("+RCV="):
                parsed = self._parse_rcv(line)
                if parsed:
                    self._pending.append(parsed)
            else:
                others.append(line)
        # Keep an unfinished +RCV line for the next read; anything else (e.g. "+OK" without its
        # newline yet) is a command response and is returned now.
        if not (self._rx_buf.startswith("+RCV=") or "+RCV=".startswith(self._rx_buf)):
            others.append(self._rx_buf)
            self._rx_buf = ""
        return others

    def _drain(self):
        """Read whatever the module has sent us so far into the pending queue."""
        if self.serial and self.serial.in_waiting > 0:
            self._split_rcv(self.serial.read(self.serial.in_waiting).decode(errors="replace"))

    def poll_messages(self):
        """Return all LoRa messages received since the last call (non-blocking, cheap)."""
        if not self.lora_available or not self.serial:
            return []
        try:
            self._drain()
        except Exception as e:
            logger.error(f"Error receiving messages: {e}")
        msgs, self._pending = self._pending, []
        return msgs

    def receive_message(self, timeout=1):
        """Return the next received message as a dict, or None (kept for older callers)."""
        if not self._pending:
            self._pending = self.poll_messages()
        return self._pending.pop(0) if self._pending else None

    def cleanup(self):
        """Close serial connection"""
        if self.serial:
            try:
                self.serial.close()
                logger.debug("LoRa serial port closed")
            except Exception as e:
                logger.error(f"Error closing serial port: {e}")

    def __del__(self):
        """Cleanup when object is destroyed"""
        self.cleanup()


if __name__ == "__main__":
    # Simple test
    logging.basicConfig(level=logging.INFO)

    print("Testing LoRa...")
    print("Note: This requires RYLR998 module connected to UART")

    if serial is None:
        print("❌ pyserial not available")
    else:
        lora = LoRaHandler()

        if lora.lora_available:
            print("✅ LoRa initialized")

            # Test sending a message
            print("\nTesting message send...")
            if lora.send_message("TEST_MESSAGE"):
                print("✅ Message sent")
            else:
                print("❌ Failed to send message")

            lora.cleanup()
        else:
            print("❌ LoRa not available")
            print("   Make sure RYLR998 is connected to /dev/serial0")
            print("   And serial port is enabled in raspi-config")
