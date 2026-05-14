"""RTC Handler for DS3231 Real-Time Clock Module"""
import logging
from datetime import datetime
try:
    import smbus2
except ImportError:
    smbus2 = None

logger = logging.getLogger(__name__)


class RTCHandler:
    """Simple interface for DS3231 RTC module"""

    def __init__(self, i2c_address=0x68, bus_number=1):
        """
        Initialize RTC handler

        Args:
            i2c_address: I2C address of DS3231 (default 0x68)
            bus_number: I2C bus number (default 1 for Pi)
        """
        self.i2c_address = i2c_address
        self.bus_number = bus_number
        self.bus = None
        self.rtc_available = False

        if smbus2 is None:
            logger.warning("smbus2 not available, using system time")
            return

        try:
            self.bus = smbus2.SMBus(bus_number)
            # Try to read from RTC to verify it's working
            self._read_time()
            self.rtc_available = True
            logger.info("RTC initialized successfully")
        except Exception as e:
            logger.warning(f"RTC not available, using system time: {e}")
            self.rtc_available = False

    def _bcd_to_dec(self, bcd):
        """Convert BCD to decimal"""
        return ((bcd // 16) * 10) + (bcd % 16)

    def _dec_to_bcd(self, dec):
        """Convert decimal to BCD"""
        return ((dec // 10) * 16) + (dec % 10)

    def _read_time(self):
        """Read time from RTC"""
        if not self.rtc_available or not self.bus:
            return None

        try:
            # Read 7 bytes starting from register 0x00
            data = self.bus.read_i2c_block_data(self.i2c_address, 0x00, 7)

            second = self._bcd_to_dec(data[0] & 0x7F)
            minute = self._bcd_to_dec(data[1])
            hour = self._bcd_to_dec(data[2] & 0x3F)
            day = self._bcd_to_dec(data[4])
            month = self._bcd_to_dec(data[5] & 0x1F)
            year = self._bcd_to_dec(data[6]) + 2000

            return datetime(year, month, day, hour, minute, second)

        except Exception as e:
            logger.debug(f"Failed to read from RTC: {e}")
            return None

    def set_time(self, dt=None):
        """
        Write time to RTC. Uses current system time if dt not provided.
        """
        if not self.rtc_available or not self.bus:
            logger.warning("RTC not available, cannot set time")
            return False
        if dt is None:
            dt = datetime.now()
        try:
            data = [
                self._dec_to_bcd(dt.second),
                self._dec_to_bcd(dt.minute),
                self._dec_to_bcd(dt.hour),
                self._dec_to_bcd(dt.weekday() + 1),
                self._dec_to_bcd(dt.day),
                self._dec_to_bcd(dt.month),
                self._dec_to_bcd(dt.year - 2000),
            ]
            self.bus.write_i2c_block_data(self.i2c_address, 0x00, data)
            logger.info(f"RTC time set to {dt}")
            return True
        except Exception as e:
            logger.error(f"Failed to set RTC time: {e}")
            return False

    def get_time(self):
        """
        Get current time

        Returns:
            datetime: Current time from RTC, or system time if RTC not available
        """
        if self.rtc_available:
            rtc_time = self._read_time()
            if rtc_time:
                return rtc_time

        # Fallback to system time
        return datetime.now()

    def get_timestamp_string(self, fmt="%Y-%m-%d %H:%M:%S"):
        """
        Get formatted timestamp string

        Args:
            fmt: strftime format string

        Returns:
            str: Formatted timestamp
        """
        return self.get_time().strftime(fmt)

    def __del__(self):
        """Cleanup"""
        if self.bus:
            try:
                self.bus.close()
            except Exception:
                pass


if __name__ == "__main__":
    # Simple test
    logging.basicConfig(level=logging.INFO)

    print("Testing RTC...")
    rtc = RTCHandler()

    print(f"RTC available: {rtc.rtc_available}")

    current_time = rtc.get_time()
    print(f"Current time: {current_time}")

    timestamp = rtc.get_timestamp_string()
    print(f"Formatted: {timestamp}")

    if rtc.rtc_available:
        print("✅ RTC working")
    else:
        print("⚠️  Using system time (RTC not detected)")
