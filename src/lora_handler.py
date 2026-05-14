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

            time.sleep(0.5)  # Wait for module to be ready

            # Test communication
            response = self._send_command("AT")
            if response and "+OK" in response:
                self.lora_available = True

                # Configure network ID
                self._send_command(f"AT+NETWORKID={network_id}")

                # Configure frequency
                self._send_command(f"AT+BAND={frequency}")

                logger.info(f"LoRa initialized: Network ID={network_id}, Frequency={frequency}MHz")
            else:
                logger.warning("LoRa module not responding")

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
            # Clear any pending data
            self.serial.reset_input_buffer()

            # Send command
            self.serial.write(f"{command}\r\n".encode())
            time.sleep(0.3)

            if expect_response:
                # Read response
                response = self.serial.read(self.serial.in_waiting or 64).decode().strip()
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

    def send_alert(self, predator_type, confidence, timestamp, date=None):
        """
        Send predator alert

        Args:
            predator_type: Type of predator (e.g., 'dog', 'cat', 'bird')
            confidence: Detection confidence (0-1)
            timestamp: Detection time string (HH:MM:SS)
            date: Detection date string (MM/DD/YYYY)

        Returns:
            bool: True if sent successfully
        """
        # Message format: PREDATOR,<type>,<time>,<confidence>,<date>
        message = f"PREDATOR,{predator_type},{timestamp},{confidence:.2f},{date or ''}"
        return self.send_message(message)

    def receive_message(self, timeout=1):
        """
        Check for incoming LoRa messages

        Args:
            timeout: Timeout in seconds

        Returns:
            dict: Parsed message {'address': int, 'length': int, 'message': str, 'rssi': int, 'snr': int}
                  or None if no message
        """
        if not self.lora_available or not self.serial:
            return None

        try:
            # Set timeout
            original_timeout = self.serial.timeout
            self.serial.timeout = timeout

            # Read data
            if self.serial.in_waiting > 0:
                data = self.serial.read(self.serial.in_waiting).decode().strip()

                # Parse message: +RCV=<address>,<length>,<message>,<RSSI>,<SNR>
                if data.startswith("+RCV="):
                    parts = data[5:].split(',', 4)  # Split into max 5 parts

                    if len(parts) >= 3:
                        parsed = {
                            'address': int(parts[0]),
                            'length': int(parts[1]),
                            'message': parts[2],
                            'rssi': int(parts[3]) if len(parts) > 3 else None,
                            'snr': int(parts[4]) if len(parts) > 4 else None
                        }

                        logger.info(f"Received LoRa message: {parsed['message']}")
                        return parsed

            # Restore timeout
            self.serial.timeout = original_timeout

        except Exception as e:
            logger.error(f"Error receiving message: {e}")

        return None

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
